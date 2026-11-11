from ankitool.core.env_template import ENV_TEMPLATE
from ankitool.core.paths import SCRIPT_DIR


def run_init(opts):
    """Writes an example .env (all services commented out) in the folder the tool reads its
    settings from. Never overwrites an existing file unless --force is given."""
    target = SCRIPT_DIR / ".env"
    if target.exists() and not opts.force:
        print(f"{target} already exists. Nothing changed (use --force to replace it).")
        return
    target.write_text(ENV_TEMPLATE, encoding="utf-8")
    print(f"Created {target}")
    print("Open it in a text editor, fill in the key(s) of the services you use and save.")
