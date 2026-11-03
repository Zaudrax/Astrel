import sys

from .platform import en_utf8

en_utf8("astrel")  # Windows : relancé en mode UTF-8 (03/11)

from .cli import main  # noqa: E402

sys.exit(main())
