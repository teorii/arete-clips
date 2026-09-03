"""Reporting things that went wrong.

Every failure gets said out loud. A recorder that quietly does nothing is worse
than one that crashes: you find out when you press the key after the moment you
wanted to keep, and by then it is gone.

Where an app can genuinely carry on, it says so and carries on. Where it
cannot, it raises. Nothing is swallowed.
"""

from __future__ import annotations


def warn(where: str, problem: BaseException | str, consequence: str = "") -> None:
    """Report a failure the app survived, and what it costs.

    The consequence is the part worth writing: "could not read the GPU name" is
    noise, "clips will not be named" is something you can act on.
    """
    detail = f"{type(problem).__name__}: {problem}" if isinstance(problem, BaseException) else str(problem)
    tail = f" ({consequence})" if consequence else ""
    print(f"[warn] {where}: {detail}{tail}")
