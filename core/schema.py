"""Shared constants. One place to change a rule or an allowed value."""

KINDS = ("lead", "faq", "support", "urgent", "spam", "job", "minor")
WANTS = ("bootcamp", "fellowship", "corporate", "unknown")
LANGS = ("bangla", "banglish", "english")
STATUSES = ("New", "Contacted", "Interested", "Paid", "Lost")

PIPELINE_KINDS = ("lead", "faq")          # these go to the sales pipeline
FOLLOWUP_STATUSES = ("New", "Contacted", "Interested")
FOLLOWUP_HOURS = 48
HIGH_VALUE_HEADCOUNT = 10                  # 10+ people = corporate-size deal

# The sample data is from 26-27 Oct 2026, so the demo clock is fixed.
# Otherwise every card would look "overdue" on the day you record the video.
DEMO_NOW = "2026-10-28 17:00"
