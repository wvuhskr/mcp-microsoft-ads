import sys

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
    # `mcp-microsoft-ads setup` / `reauth` are the human-run helpers; no argument (how
    # an MCP client launches it) serves.
    if len(sys.argv) > 1 and sys.argv[1] in ("setup", "reauth"):
        cmd = sys.argv.pop(1)
        if cmd == "setup":
            from . import setup_wizard
            sys.exit(setup_wizard.main())
        from . import reauth
        sys.exit(reauth.main())
    settings.load()  # loud failure on a bad advertiser.yaml, before serving starts
    mcp.run()


if __name__ == "__main__":
    main()
