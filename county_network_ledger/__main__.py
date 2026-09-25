"""支持 `python3 -m county_network_ledger` 直接调用命令行接口。"""

from .interfaces.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
