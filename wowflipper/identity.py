"""Conservative identities shared across valuation, comparisons and commitments."""
import json
from .models import integer

PET_CAGE = 82800
FIELDS = {'species_id': 'pet_species_id', 'level': 'pet_level', 'quality': 'pet_quality_id', 'breed_id': 'pet_breed_id'}

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
