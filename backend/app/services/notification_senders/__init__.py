"""Scheduled notification senders.

Each module here answers one question — "what happened today that someone watching a
ticker would want to know?" — and hands the answer to `push_dispatch_service`. None of
them decides WHETHER a given user gets it: preferences, per-category caps, quiet hours
and dedup all live in the dispatcher, so a new sender cannot accidentally bypass a
user's opt-out.

Shape every sender follows:

    async with claimed_job(JOB_X) as run:      # cross-instance daily claim
        if run is None:
            return                             # another instance has it, or done today
        ...select candidates...                # the part with the bugs in it
        run.notified = await dispatch(...)     # fan-out
        run.success = True                     # explicit; the default is False
"""
