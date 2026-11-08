"""支持 ``python -m bookcard``。"""

import sys

from bookcard.cli import main

if __name__ == "__main__":
    sys.exit(main())
