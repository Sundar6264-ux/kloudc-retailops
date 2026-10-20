"""Clover adapter (website-ready track D; multi-store plan 4.7, M3).

- ``client``: REST v3 GETs, region hosts, paging, 90-day windows, rate limits.
- ``mapping``: Clover JSON -> the POS-neutral models.
- ``auth``: manual token and OAuth v2 (Connect with Clover), token refresh.
- ``connector``: ``CloverConnector`` (registered as ``POS_PROVIDER=clover``).
"""
