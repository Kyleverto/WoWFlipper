"""Validated filters. Filter defaults are unvalidated provisional assumptions."""
from dataclasses import dataclass, fields
from decimal import Decimal
from .models import integer, decimal

@dataclass(frozen=True)
class Settings:
    capital: int = 300_000_000
    # Retained only to read and preserve previously saved settings.
    stronger_demand_budget: int = 200_000_000
    longer_term_budget: int = 50_000_000
    reserve: int = 50_000_000
    item_exposure: int = 30_000_000
    discount: Decimal = Decimal('0.15')
    auction_cut: Decimal = Decimal('0.05')
    resale_factor: Decimal = Decimal('1')
    min_profit: int = 1_000_000
    min_roi: Decimal = Decimal('0.10')
    min_sale_rate: Decimal = Decimal('0.01')
    min_sold_per_day: Decimal = Decimal('0.01')
    max_tsm_age_hours: int = 48
    max_auction_age_hours: int = 2
    max_copies: int = 1
    extra_costs: int = 0
    home_realm: str = 'Agamaggan'
    region: str = 'us'
    buying_realms: tuple = ('agamaggan',)
    selling_realms: tuple = ()
    categories: tuple = ('recipe', 'mount', 'toy')

    @classmethod
    def from_dict(cls, data: dict):
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise ValueError('Unknown settings')
        default = cls()
        values = {}
        for field in fields(cls):
            value = data.get(field.name, getattr(default, field.name))
            if isinstance(getattr(default, field.name), Decimal):
                value = decimal(value, 1 if field.name in {'discount','auction_cut','min_sale_rate'} else None)
            elif isinstance(getattr(default, field.name), int):
                value = integer(value, 1 if field.name in {'capital','max_copies','max_tsm_age_hours','max_auction_age_hours'} else 0)
            elif field.name in {'buying_realms','selling_realms','categories'}:
                import re
                if not isinstance(value,(list,tuple)) or any(not isinstance(x,str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',x) for x in value):
                    raise ValueError('Expected realm slugs or category names')
                value=tuple(dict.fromkeys(value))
                if field.name=='categories' and set(value)-{'recipe','mount','toy'}: raise ValueError('Unsupported category')
            elif not isinstance(value, str) or not value.strip():
                raise ValueError('Expected nonempty text')
            values[field.name] = value
        result = cls(**values)
        if result.region != 'us':
            raise ValueError('Only retail US is supported')
        if result.reserve > result.capital:
            raise ValueError('Reserve exceeds capital')
        if result.item_exposure > result.capital:
            raise ValueError('Item exposure exceeds capital')
        return result

    def to_dict(self):
        return {f.name: str(v) if isinstance(v := getattr(self,f.name), Decimal) else v for f in fields(self)}
