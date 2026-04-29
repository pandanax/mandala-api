"""Точка входа: ``python -m mandala.adapters.telegram`` (тикет 9)."""

from __future__ import annotations

import logging
import sys

from mandala.adapters.telegram.polling import run_polling_forever


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    run_polling_forever()


if __name__ == "__main__":
    main()
