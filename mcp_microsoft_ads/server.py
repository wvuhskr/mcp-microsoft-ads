from . import settings
from .app import mcp
from .tools import (
    adgroups_write,  # noqa: F401
    ads_write,  # noqa: F401
    audiences,  # noqa: F401
    campaigns_write,  # noqa: F401  — importing registers tools
    confirm,  # noqa: F401
    conversions,  # noqa: F401
    entities,  # noqa: F401
    extensions_write,  # noqa: F401
    geo_read,  # noqa: F401
    geo_write,  # noqa: F401
    infra,  # noqa: F401
    insight,  # noqa: F401
    keywords_write,  # noqa: F401
    media,  # noqa: F401
    negatives_read,  # noqa: F401
    negatives_write,  # noqa: F401
    pmax,  # noqa: F401
    reads_misc,  # noqa: F401
    remove,  # noqa: F401
    reporting,  # noqa: F401
    schedule,  # noqa: F401
    status,  # noqa: F401
)


def main():
    settings.load()  # loud failure on a bad advertiser.yaml, before serving starts
    mcp.run()


if __name__ == "__main__":
    main()
