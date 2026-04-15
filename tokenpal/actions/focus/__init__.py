"""Phase 3 focus tools — pomodoro, proactive nudges, habit logging.

All user-initiated tools are safe. Proactive nudges (stretch_reminder,
water_reminder, eye_break, bedtime_wind_down) register a recurring job
with the brain's ProactiveScheduler and pause automatically during
active conversations and when a sensitive app is in the foreground.
"""
