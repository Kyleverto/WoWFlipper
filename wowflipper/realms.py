"""Eligibility derived from Blizzard's canonical connected-realm index."""


def auction_realm(realm):
    return realm.get('is_auction_realm') is True


def selectable_us_realm(realm):
    return realm.get('category') == 'United States' and auction_realm(realm)
