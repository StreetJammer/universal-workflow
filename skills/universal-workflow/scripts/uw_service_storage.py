"""Bounded storage admission for the optional service, not the local owner CLI."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat


MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_NAMESPACE_BYTES = 64 * 1024 * 1024
MAX_SERVICE_BYTES = 1024 * 1024 * 1024
MAX_STATE_ENTRIES = 8192
MAX_STATE_DEPTH = 8
# Covers WAL frame headers, shared memory, fixed initialization files, and
# checkpoint overhead in addition to two complete database images.
SQLITE_HEADROOM = 1024 * 1024
APPEND_LIMITS = {'history': 10_000, 'events': 10_000, 'reviews': 1000, 'proposals': 1000}


class ServiceError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class StorageBudget:
    """One bounded metadata snapshot plus conservative, non-reusable reservations.

    The caller holds the shared service policy lock until every store closes.
    Reservations remain held when inherited stores close: their initialization
    cannot spend the pending target transaction's future database/WAL capacity.
    """
    def __init__(self, directory):
        self.root = Path(directory) / 'state'
        self.total = 0
        self.namespaces = {}
        self.reservations = {}
        self.entry_reservations = {}
        entries = 0
        pending = [(self.root, ())]
        while pending:
            path, parts = pending.pop()
            with os.scandir(path) as children:
                for child in children:
                    entries += 1
                    if entries > MAX_STATE_ENTRIES or len(parts) >= MAX_STATE_DEPTH:
                        raise ServiceError(413, 'Service state exceeds its entry limit')
                    info = child.stat(follow_symlinks=False)
                    reparse = getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
                    if stat.S_ISLNK(info.st_mode) or reparse:
                        raise ServiceError(413, 'Unsupported entry in service state')
                    relative = (*parts, child.name)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append((Path(child.path), relative))
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise ServiceError(413, 'Unsupported entry in service state')
                    self.total += info.st_size
                    if self.total > MAX_SERVICE_BYTES:
                        raise ServiceError(413, 'Service storage capacity exceeded')
                    width = 2 if relative[0] == 'organization' else 3
                    if len(relative) <= width:
                        continue  # Still charged globally, even outside a namespace.
                    target = relative[:width]
                    usage = self.namespaces.setdefault(target, {'total': 0, 'database': 0, 'wal': 0, 'shm': 0})
                    usage['total'] += info.st_size
                    if relative[width:] == ('.universal-workflow', 'memory.sqlite3'):
                        usage['database'] = info.st_size
                    elif relative[width:] == ('.universal-workflow', 'memory.sqlite3-wal'):
                        usage['wal'] = info.st_size
                    elif relative[width:] == ('.universal-workflow', 'memory.sqlite3-shm'):
                        usage['shm'] = info.st_size
                    if usage['total'] > MAX_NAMESPACE_BYTES or usage['database'] > MAX_DATABASE_BYTES:
                        raise ServiceError(413, 'Namespace storage capacity exceeded')
        self.entries = entries

    def reserve(self, target):
        target = tuple(target)
        if target in self.reservations:
            return
        usage = self.namespaces.get(target, {'total': 0, 'database': 0, 'wal': 0, 'shm': 0})
        # Existing stores can open an init lock and WAL/SHM. New stores also
        # create their path, config, database, ignore file and atomic temporary
        # file. Leave enough capacity to reopen them after initialization.
        entry_growth = 3 if usage['database'] else 12
        namespace = self.root.joinpath(*target) / '.universal-workflow'
        if usage['database'] and not (namespace / '.gitignore').is_file():
            entry_growth += 2  # Missing ignore file plus its atomic temporary.
        if self.entries + sum(self.entry_reservations.values()) + entry_growth > MAX_STATE_ENTRIES:
            raise ServiceError(413, 'Service lacks state entry headroom')
        other = usage['total'] - usage['database'] - usage['wal'] - usage['shm']
        # One outer transaction, no dirty-page spills, and an initially empty
        # WAL bound its peak to one database image plus one WAL image. Reserve
        # this for reads too: opening/closing a legacy WAL may checkpoint it.
        # A pre-service WAL can contain several database images. Its checkpoint
        # grows the main file before removing that WAL, so include both peaks.
        peak = max(usage['total'] + max(MAX_DATABASE_BYTES - usage['database'], usage['wal']) + SQLITE_HEADROOM,
                   other + 2 * MAX_DATABASE_BYTES + SQLITE_HEADROOM)
        if peak > MAX_NAMESPACE_BYTES:
            raise ServiceError(413, 'Namespace lacks SQLite storage headroom')
        additional = peak - usage['total']
        if self.total + sum(self.reservations.values()) + additional > MAX_SERVICE_BYTES:
            raise ServiceError(413, 'Service lacks SQLite storage headroom')
        self.reservations[target] = additional
        self.entry_reservations[target] = entry_growth
        if usage['database']:
            # A crash-left WAL may describe a logical DB larger than the main
            # file. Reject it through a read-only connection: closing an RW
            # connection after rejection could otherwise checkpoint that DB.
            db = sqlite3.connect((namespace / 'memory.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                logical_bytes = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
            finally:
                db.close()
            if logical_bytes > MAX_DATABASE_BYTES:
                raise ServiceError(413, 'Namespace database capacity exceeded')


class DeferredConnection:
    """Keep core helpers' direct commit calls inside the service transaction."""
    def __init__(self, connection):
        self.raw = connection
        self.deferred = False

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def commit(self):
        if not self.deferred:
            self.raw.commit()


class StoreQuota:
    def __init__(self, store):
        self.store = store
        self.connection = DeferredConnection(store.db)
        db = self.connection.raw
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        maximum = MAX_DATABASE_BYTES // page_size
        if pages > maximum:
            raise ServiceError(413, 'Namespace database capacity exceeded')
        if db.execute('PRAGMA max_page_count=' + str(maximum)).fetchone()[0] > maximum:
            raise ServiceError(413, 'Namespace database capacity exceeded')
        db.execute('PRAGMA cache_spill=OFF')
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('PRAGMA journal_size_limit=0')
        db.execute('PRAGMA wal_autocheckpoint=0')
        # TEMP counters/triggers are connection-local, are rolled back with the
        # main transaction, and never alter the owner's schema or CLI behavior.
        db.execute('CREATE TEMP TABLE service_counts(name TEXT PRIMARY KEY, amount INTEGER, maximum INTEGER)')
        for table, limit in APPEND_LIMITS.items():
            count = db.execute('SELECT COUNT(*) FROM (SELECT 1 FROM ' + table + ' LIMIT ?)', (limit + 1,)).fetchone()[0]
            if count > limit:
                raise ServiceError(413, 'Namespace audit capacity exceeded')
            db.execute('INSERT INTO service_counts VALUES(?,?,?)', (table, count, limit))
            db.executescript(f"""
                CREATE TEMP TRIGGER service_{table}_insert BEFORE INSERT ON main.{table}
                BEGIN
                    SELECT CASE WHEN (SELECT amount>=maximum FROM service_counts WHERE name='{table}')
                        THEN RAISE(ABORT, 'service audit capacity exceeded') END;
                    UPDATE service_counts SET amount=amount+1 WHERE name='{table}';
                END;
                CREATE TEMP TRIGGER service_{table}_delete AFTER DELETE ON main.{table}
                BEGIN
                    UPDATE service_counts SET amount=amount-1 WHERE name='{table}';
                END;
            """)
        db.commit()
        store.db = self.connection

    def checkpoint(self):
        result = self.connection.raw.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if result[0] != 0:
            raise ServiceError(503, 'Namespace checkpoint is busy')

    @contextmanager
    def transaction(self):
        if self.connection.deferred:
            yield
            return
        # A long-lived owner reader must not let service requests accumulate WAL.
        self.checkpoint()
        db = self.connection.raw
        db.execute('BEGIN IMMEDIATE')
        self.connection.deferred = True
        try:
            yield
            db.commit()
        except BaseException as error:
            db.rollback()
            message = str(error)
            if isinstance(error, sqlite3.Error) and ('service audit capacity exceeded' in message or 'database or disk is full' in message):
                raise ServiceError(413, 'Namespace storage capacity reached') from error
            raise
        finally:
            self.connection.deferred = False
            # On a post-commit reader race, the transaction is already durable:
            # retain its result and WAL. The next writer must checkpoint first.
            try:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            except sqlite3.Error:
                # Cleanup cannot undo a durable commit or replace the original
                # rollback error. A subsequent write must pass checkpoint again.
                pass
