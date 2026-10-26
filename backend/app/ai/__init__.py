"""The AI brain: Claude Code on the owner's computer does the thinking for the roles that write and review.

* ``gateway``: the only door to the model (permissions, readiness, limits, provenance, usage).
* ``schema``: strict checking of what the model returns.
* ``prompts``: what each role is asked, built only from recorded facts.
* ``drafting``: queue handlers that draft briefs, lyrics/scripts and ideas.
* ``review``: manager review of finished work (mechanical re-verification; model-assisted review of creative drafts).
* ``conductor``: moves a video through the steps no worker task covers.
"""
