# Security scope

The current release is a local, single-project tool. It is not a security boundary against another process with filesystem access. Actor labels are not authentication. Owner policy, secrets, and sensitive native-memory data should remain outside the writable project store.

Use private vulnerability reporting if the repository exposes that option. If it is unavailable, open an issue asking for a private reporting channel without disclosing exploit details or secrets. Do not paste user memory databases or credentials into public issues.

The credential filter is a best-effort convenience, not a data-loss-prevention system. Keep records small and sanitized. Evaluate untrusted workloads in an isolated account/container; a configured evaluator is arbitrary code selected by the project owner. Review workers inherit the local CLI's available account/configuration and normal billing.
