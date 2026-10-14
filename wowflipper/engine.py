"""Pure calculations; regional sales statistics are assumptions, not proceeds guarantees."""
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext, ROUND_FLOOR
from .models import Candidate, Auction, is_fresh, integer, decimal


def _simple(auction):
    if not isinstance(auction.metadata, dict) or auction.metadata.get("simple_identity") is not True:
        return False
    if auction.variant_key is None:
        return True
    try:
        variants = json.loads(auction.variant_key)
        return isinstance(variants, dict) and all(
            k in {"context", "bonus_lists", "modifiers"} and v in (0, [], None)
            for k, v in variants.items())
    except (ValueError, TypeError):
        return False


def _precision(settings):
    values = (settings.resale_factor, settings.discount, settings.auction_cut, settings.min_roi)
    return max(80, sum(len(v.as_tuple().digits) + abs(v.as_tuple().exponent) for v in values) + 80)


def _amounts(auction, valuation, settings, resale_override=None):
    resale = Decimal(integer(resale_override["unit_price"], 1)) if resale_override else Decimal(valuation.avg_sale_price) * settings.resale_factor
    ceiling = resale * (1 - settings.discount) * auction.quantity
    proceeds = int((resale * auction.quantity * (1 - settings.auction_cut)).to_integral_value(rounding=ROUND_FLOOR))
    profit = proceeds - auction.buyout - settings.extra_costs
    return resale, ceiling, proceeds, profit, Decimal(profit) / auction.buyout


def exclusion_reason(auction, valuation, settings, now, resale_override=None):
    """First inspectable exclusion reason, or None when actionable."""
    if auction.region != settings.region or auction.region != 'us':
        return 'unsupported_region'
    if not is_fresh(auction.source_time, settings.max_auction_age_hours, now):
        return 'stale_or_unknown_auction_source'
    if not is_fresh(valuation.updated_at, settings.max_tsm_age_hours, now):
        return 'stale_or_unknown_valuation_source'
    try:
        integer(auction.buyout, 1)
        integer(auction.quantity, 1)
        integer(valuation.avg_sale_price, 1)
        decimal(valuation.sale_rate, 1)
        decimal(valuation.sold_per_day)
    except (ValueError, TypeError):
        return 'invalid_valuation_or_listing'
    if auction.item_id != valuation.item_id:
        return 'valuation_identity_mismatch'
    if auction.category in {'pet', 'battle-pet'}:
        return 'unsupported_pet'
    if auction.category in {'gear', 'armor', 'weapon'}:
        return 'ambiguous_gear_valuation'
    if not isinstance(auction.metadata, dict):
        return 'missing_item_metadata'
    if not auction.category or auction.category == 'unknown':
        return 'unknown_item_category'
    if not _simple(auction):
        return 'unsupported_complex_variant'
    if auction.metadata.get('tradeability') is False:
        return 'unsuitable_tradeability'
    if auction.category not in settings.categories:
        return 'category_filtered'
    if auction.quantity > settings.max_copies:
        return 'quantity_limit'
    if auction.buyout > min(settings.item_exposure, settings.capital - settings.reserve):
        return 'purchase_budget_exceeded'
    if valuation.sale_rate < settings.min_sale_rate or valuation.sold_per_day < settings.min_sold_per_day:
        return 'demand_below_threshold'
    with localcontext() as ctx:
        ctx.prec = _precision(settings)
        _, ceiling, _, profit, roi = _amounts(auction, valuation, settings, resale_override)
        if auction.buyout > ceiling:
            return 'discount_below_threshold'
        if profit < settings.min_profit or roi < settings.min_roi:
            return 'return_below_threshold'
    return None


def evaluate(auction, valuation, settings, now, resale_override=None):
    if exclusion_reason(auction, valuation, settings, now, resale_override):
        return None
    with localcontext() as ctx:
        ctx.prec = _precision(settings)
        resale, _, proceeds, profit, roi = _amounts(auction, valuation, settings, resale_override)
    risks = ['regional_demand_not_destination_specific', 'resale_price_is_assumption']
    if auction.metadata.get('tradeability') is not True:
        risks.append('unverified_tradeability')
    return Candidate((auction.region, auction.house_id, auction.auction_id), auction, valuation,
                     proceeds, profit, roi, (), tuple(risks),
                     'Full listing purchase; estimated proceeds floor after auction cut, less purchase and per-listing costs. Regional sales statistics do not guarantee destination demand or resale proceeds.', resale, dict(resale_override) if resale_override else None)


def rank(candidates, strategy='general'):
    """Order by estimated profit and deduplicate; legacy strategy names are inert."""
    if strategy not in {'general', 'stronger-demand', 'longer-term'}:
        raise ValueError('Unknown ranking strategy')
    def ordering(c):
        v = c.valuation
        metrics = (c.profit, c.roi, v.sold_per_day, v.sale_rate)
        return tuple(x.copy_negate() if isinstance(x, Decimal) else -x for x in metrics) + (c.key,)
    result, seen = [], set()
    for c in sorted(candidates, key=ordering):
        if c.key not in seen:
            result.append(c)
            seen.add(c.key)
    return result


def index_destinations(snapshots):
    """Build one item index per connected house, sharing sibling realm evidence."""
    index = {}
    for snap in snapshots.values():
        if snap is None or snap.source_key in index:
            continue
        items = {}
        for auction in snap.records:
            if isinstance(auction, Auction):
                items.setdefault(auction.item_id, []).append(auction)
        index[snap.source_key] = items
    return index


def compare_destinations(candidate, snapshots, settings, now=None, index=None):
    """snapshots maps configured realm slug to Snapshot. Missing keys are explicit absent evidence.

    Returned unit asks are Decimal copper, preserving indivisible stack prices.
    now is injectable; defaults to current UTC. This performs no repository/network access.
    """
    now = now or datetime.now(timezone.utc)
    results = []
    with localcontext() as ctx:
        ctx.prec = _precision(settings)
        resale = candidate.assumed_unit_resale if candidate.assumed_unit_resale is not None else Decimal(candidate.valuation.avg_sale_price) * settings.resale_factor
        for slug in settings.selling_realms:
            snap = snapshots.get(slug)
            risks, matches = [], []
            same_house = bool(snap and snap.source_key == f'auctions:us:{candidate.auction.house_id}')
            if not same_house:
                risks.append('unverified_transfer_eligibility')
            if snap is None:
                risks.append('absent_destination_evidence')
            elif not is_fresh(snap.source_time, settings.max_auction_age_hours, now):
                risks.append('stale_destination_evidence')
            else:
                records = snap.records if index is None else index.get(snap.source_key, {}).get(candidate.auction.item_id, ())
                for a in records:
                    if not isinstance(a, Auction) or a.item_id != candidate.auction.item_id or a.region != candidate.auction.region:
                        continue
                    if snap.source_key != f'auctions:us:{a.house_id}':
                        continue
                    if (a.region, a.house_id, a.auction_id) == candidate.key:
                        continue
                    if not _simple(a):
                        continue
                    if a.category != candidate.auction.category or a.metadata.get('tradeability') is False:
                        continue
                    try:
                        integer(a.quantity, 1)
                        integer(a.buyout, 1)
                    except ValueError:
                        continue
                    matches.append(a)
                if not matches:
                    risks.append('absent_destination_evidence')
            lowest = min((Decimal(a.buyout) / a.quantity for a in matches), default=None)
            if lowest is not None and lowest < resale:
                risks.append('competition_below_resale')
            results.append({'realm': slug, 'snapshot_id': snap.id if snap else None,
                            'source_time': snap.source_time if snap else None,
                            'quantity': sum(a.quantity for a in matches), 'listing_count': len(matches),
                            'lowest_unit_ask': lowest, 'assumed_unit_resale': resale,
                            'risks': tuple(risks),
                            'explanation': 'Exact simple-item asking prices and quantities; asks are not confirmed sales. Transfer eligibility requires verification.'})
    return results
