"""Phase 1 skills and Phase 2 stubs."""

from core.skills.charging_tips import ChargingTipsSkill
from core.skills.climate import ClimateSkill
from core.skills.flash_lights import FlashLightsSkill
from core.skills.morning_briefing import MorningBriefingSkill
from core.skills.news_watch import NewsWatchSkill
from core.skills.onboarding import OnboardingSkill
from core.skills.status import StatusSkill
from core.skills.trip_advisor import TripAdvisorSkill

PHASE_1 = (StatusSkill, FlashLightsSkill, ClimateSkill, MorningBriefingSkill)
PHASE_2_STUBS = (OnboardingSkill, TripAdvisorSkill, ChargingTipsSkill, NewsWatchSkill)
