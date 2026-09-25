"""命令行入口：python -m county_network_ledger ..."""
from .interfaces.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
