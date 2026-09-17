"""point the built-in DeepSeek provider at a model that still exists

`bootstrap._ensure_llm_provider` seeds the provider create-if-missing: once the
row exists, the file is no longer consulted. So commit 6157bed renamed the model
in code and changed nothing on an already-running instance — it kept calling
`deepseek-chat`, which DeepSeek retired. The request comes back with an empty
body, `completion_tokens=0`, and the run completes with a blank digest.

DB-is-truth, so this is a *data* migration, and a deliberately narrow one: only
rows whose model is a renamed-out DeepSeek name, and only when the endpoint is
DeepSeek's own (an empty api_base resolves to it for a `deepseek/` model). A
provider pointing at a proxy or a self-hosted gateway keeps whatever model name
that endpoint expects.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_deepseek_model"
down_revision = "0015_github_drop_follow"
branch_labels = None
depends_on = None

# Retired name -> current one, same API, same style.
RENAMES = (("deepseek/deepseek-chat", "deepseek/deepseek-v4-flash"),)

# "" and NULL both mean "use litellm's default for this provider", which for a
# `deepseek/` model is api.deepseek.com.
DEEPSEEK_ENDPOINT = "(api_base IS NULL OR api_base = '' OR api_base LIKE '%deepseek%')"


def _update(old: str, new: str, *, to_old: bool) -> None:
    src, dst = (new, old) if to_old else (old, new)
    op.get_bind().execute(
        sa.text(
            f"UPDATE llm_providers SET model = :dst WHERE model = :src AND {DEEPSEEK_ENDPOINT}"
        ),
        {"src": src, "dst": dst},
    )


def upgrade() -> None:
    for old, new in RENAMES:
        _update(old, new, to_old=False)


def downgrade() -> None:
    """Restore the old name — which no longer resolves upstream, but is what the
    row said before. Only rows still on the DeepSeek endpoint are touched."""
    for old, new in RENAMES:
        _update(old, new, to_old=True)
