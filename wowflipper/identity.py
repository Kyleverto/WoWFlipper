"""Conservative identities shared across valuation, comparisons and commitments."""
import json
from .models import integer, MAX_SAFE_INTEGER

PET_CAGE = 82800
FIELDS = {'species_id': 'pet_species_id', 'level': 'pet_level', 'quality': 'pet_quality_id', 'breed_id': 'pet_breed_id'}

def simple_item_identity(auction,category=None):
    """Whether raw ordinary-item variants retain the same collectible identity.

    Only verified mount/decor categories may ignore the listed origin metadata.
    This does not grant the separately required metadata.simple_identity flag.
    """
    def canonical_integer(value,minimum=0):
        return type(value) is int and minimum<=value<=MAX_SAFE_INTEGER
    if not canonical_integer(auction.item_id,1) or auction.item_id==PET_CAGE: return False
    if auction.variant_key is None: return True
    category=auction.category if category is None else category
    allowed_modifiers={28} if category=='mount' else {28,39,40,42} if category=='decor' else set()
    try:
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result: raise ValueError('Duplicate item field')
                result[key]=value
            return result
        variants=json.loads(auction.variant_key,object_pairs_hook=unique)
        if not isinstance(variants,dict) or set(variants)-{'context','bonus_lists','modifiers'}: return False
        context=variants.get('context')
        if context is not None:
            if not canonical_integer(context): return False
            if category not in {'mount','decor'} and context!=0: return False
        bonuses=variants.get('bonus_lists')
        if bonuses is not None and (type(bonuses) is not list or bonuses): return False
        modifiers=variants.get('modifiers')
        if modifiers is None: return True
        if type(modifiers) is not list: return False
        seen=set()
        for modifier in modifiers:
            if not isinstance(modifier,dict) or set(modifier)!={'type','value'}: return False
            kind,value=modifier['type'],modifier['value']
            if not canonical_integer(kind) or not canonical_integer(value): return False
            if kind not in allowed_modifiers or kind in seen: return False
            seen.add(kind)
        return True
    except (ValueError,TypeError):
        return False

def discovery_item_identity(auction,categories=('recipe','mount','toy','decor')):
    """Permit bounded metadata discovery before mount/decor classification exists."""
    # These policies are nested. Select their union without decoding each raw
    # auction up to four times during a cold refresh.
    categories=tuple(categories)
    for category in ('decor','mount','recipe','toy'):
        if category in categories: return simple_item_identity(auction,category=category)
    return False

def pet_details(auction):
    if auction.item_id != PET_CAGE:
        return None
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('Duplicate pet field')
                result[key] = value
            return result
        raw = json.loads(auction.variant_key, object_pairs_hook=unique)
        if not isinstance(raw, dict): return None
        pet = {key: integer(raw[field], 0 if key == 'quality' else 1) for key, field in FIELDS.items()}
        if pet['level'] > 25 or pet['quality'] > 5: return None
        # Unknown pet identity fields cannot be silently merged into a known variant.
        if any(key.startswith('pet_') and key not in FIELDS.values() for key in raw): return None
        return pet
    except (ValueError, TypeError, KeyError):
        return None

def pet_identity(pet):
    return 'pet:{species_id}:{level}:{quality}:{breed_id}'.format(**pet)

def market_key(auction):
    pet = pet_details(auction)
    return pet_identity(pet) if pet else f'item:{auction.item_id}'

def override_key(auction):
    return market_key(auction) if pet_details(auction) else auction.item_id

def parse_pet_identity(value):
    if not isinstance(value, str): raise ValueError('Expected canonical pet identity')
    parts = value.split(':')
    if len(parts) != 5 or parts[0] != 'pet': raise ValueError('Expected canonical pet identity')
    pet = {key: integer(part, 0 if key == 'quality' else 1) for key, part in zip(FIELDS, parts[1:])}
    if pet['level'] > 25 or pet['quality'] > 5 or pet_identity(pet) != value:
        raise ValueError('Expected canonical pet identity')
    return pet
