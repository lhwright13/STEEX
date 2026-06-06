"""Notification primitives for STEEX (P0-3+).

`user_updates` is the single canonical stream of user-facing events that feeds
both the iMessage notifications (P1) and the dashboard "Today's Events" /
event-trigger panels (P3). Producers write one record; every surface reads it.
"""
