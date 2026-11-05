"""TODO: Phase 2 — watch public recall and safety notices.

Stay silent unless an item needs the owner to act. Do not poll the vehicle for this.
"""


class NewsWatchSkill:
    name = "news_watch"
    phase = 2

    def run(self, ctx) -> None:
        """TODO: Phase 2 — notify only when a recall or safety notice needs action."""
        raise NotImplementedError("news-watch is a Phase 2 stub")
