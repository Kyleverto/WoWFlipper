"""Origin metadata may be ignored only for verified collectible categories."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest

from wowflipper import identity
from wowflipper.engine import evaluate, compare_destinations, exclusion_reason
from wowflipper.models import Auction, Snapshot, Valuation, MAX_SAFE_INTEGER
from wowflipper.settings import Settings

NOW=datetime(2026,10,16,tzinfo=timezone.utc)
MOUNT='{"context":14,"bonus_lists":[],"modifiers":[{"type":28,"value":1234}]}'
DECOR='{"context":13,"bonus_lists":[],"modifiers":[{"type":28,"value":2814},{"type":39,"value":1049},{"type":40,"value":2952},{"type":42,"value":12}]}'
A=Auction('us',1,1,245392,1,1000,None,NOW,'mount',{'simple_identity':True,'tradeability':True})
V=Valuation(245392,'Collectible',10000000,Decimal('.5'),Decimal('5'),10000000,10000000,NOW)
S=replace(Settings(),categories=('mount','decor','recipe','toy'),selling_realms=('away',))


@pytest.mark.parametrize('category,variant',[('mount',MOUNT),('decor',DECOR)])
def test_verified_collectible_origin_metadata_does_not_exclude_candidate(category,variant):
    auction=replace(A,category=category,variant_key=variant)
    candidate=evaluate(auction,V,S,NOW)
    assert candidate is not None
    assert candidate.auction.variant_key==variant
    assert candidate.proceeds==9500000


@pytest.mark.parametrize('variant',[None,'{}','{"context":0,"bonus_lists":[],"modifiers":[]}',
    '{"context":null,"bonus_lists":null,"modifiers":null}'])
@pytest.mark.parametrize('category',['recipe','toy','mount','decor'])
def test_plain_item_shapes_remain_eligible(category,variant):
    auction=replace(A,category=category,variant_key=variant)
    assert identity.simple_item_identity(auction) is True
    assert evaluate(auction,V,S,NOW) is not None


@pytest.mark.parametrize('variant,category',[(MOUNT,'mount'),(DECOR,'decor'),('{"context":0,"modifiers":[{"type":28,"value":0}]}','mount')])
def test_discovery_accepts_verified_shape_before_category_and_cached_flag_known(variant,category):
    auction=replace(A,category='unknown',variant_key=variant,metadata={'simple_identity':False})
    assert identity.discovery_item_identity(auction) is True
    assert identity.simple_item_identity(auction,category=category) is True
    assert evaluate(replace(auction,category=category),V,S,NOW) is None


@pytest.mark.parametrize('category',['recipe','toy','gear','armor','weapon','unknown'])
def test_origin_metadata_is_not_ignored_for_other_categories(category):
    auction=replace(A,category=category,variant_key=MOUNT)
    assert identity.simple_item_identity(auction) is False
    assert evaluate(auction,V,S,NOW) is None


@pytest.mark.parametrize('variant',[
    '{"bonus_lists":[123]}',
    '{"context":true}', '{"context":"14"}', '{"context":-1}',
    '{"context":[]}', '{"context":1.5}',
    '{"context":0,"context":14}', '{"bonus_lists":[],"bonus_lists":[]}',
    '{"modifiers":[{"type":28,"value":1,"value":2}]}',
    '{"modifiers":[{"type":28,"value":1},{"type":28,"value":2}]}',
    '{"modifiers":[{"type":38,"value":1}]}',
    '{"modifiers":[{"type":29,"value":1}]}',
    '{"modifiers":[{"type":999,"value":1}]}',
    '{"modifiers":[{"type":true,"value":1}]}',
    '{"modifiers":[{"type":"28","value":1}]}',
    '{"modifiers":[{"type":28,"value":true}]}',
    '{"modifiers":[{"type":28,"value":"1"}]}',
    '{"modifiers":[{"type":28,"value":-1}]}',
    '{"modifiers":[{"type":28,"value":1.0}]}',
    '{"modifiers":[{"type":28}]}', '{"modifiers":[{}]}',
    '{"modifiers":[{"type":28,"value":1,"extra":0}]}',
    '{"modifiers":{}}', '{"modifiers":[null]}',
    '{"bonus_lists":0}', '{"bonus_lists":false}',
    '{"pet_species_id":39}', '{"pet_level":1}', '{"unknown":0}',
    '[]', 'null', 'false', '0', '"text"', '{broken',
    json.dumps({'context':MAX_SAFE_INTEGER+1}),
    json.dumps({'modifiers':[{'type':28,'value':MAX_SAFE_INTEGER+1}]}),
])
def test_meaningful_or_malformed_variants_never_enter_discovery_or_comparisons(variant):
    for category in ('mount','decor','recipe','toy'):
        auction=replace(A,category=category,variant_key=variant)
        assert identity.simple_item_identity(auction) is False
        assert identity.discovery_item_identity(auction) is False
        assert evaluate(auction,V,S,NOW) is None


def test_mount_rejects_decor_crafting_modifiers():
    assert identity.simple_item_identity(replace(A,variant_key=DECOR)) is False
    assert identity.discovery_item_identity(replace(A,variant_key=DECOR,category='unknown')) is True


def test_destination_compares_exact_collectible_item_across_origin_shapes():
    origin=replace(A,category='decor',variant_key=DECOR)
    candidate=evaluate(origin,V,S,NOW)
    assert candidate is not None
    destination=replace(origin,house_id=2,auction_id=2,variant_key=MOUNT,buyout=2000)
    bad_quality=replace(destination,auction_id=3,variant_key='{"modifiers":[{"type":38,"value":2}]}')
    bad_bonus=replace(destination,auction_id=4,variant_key='{"bonus_lists":[123]}')
    wrong_item=replace(destination,auction_id=5,item_id=999)
    stale_flag=replace(destination,auction_id=6,metadata={'simple_identity':False,'tradeability':True})
    snap=Snapshot('auctions:us:2',NOW,NOW,None,'fixture',(destination,bad_quality,bad_bonus,wrong_item,stale_flag))
    comparison=compare_destinations(candidate,{'away':snap},S,NOW)[0]
    assert comparison['listing_count']==1 and comparison['quantity']==1
    assert comparison['lowest_unit_ask']==Decimal(2000)


def test_pet_cage_is_never_an_ordinary_simple_item():
    auction=replace(A,item_id=82800,variant_key=None)
    assert identity.simple_item_identity(auction) is False
    assert identity.discovery_item_identity(auction) is False
    assert exclusion_reason(auction,replace(V,item_id=82800),S,NOW)=='unsupported_complex_variant'


@pytest.mark.parametrize('item_id',[False,True,0,-1,'245392',MAX_SAFE_INTEGER+1])
def test_invalid_item_id_is_not_a_safe_ordinary_identity(item_id):
    auction=replace(A,item_id=item_id)
    assert identity.simple_item_identity(auction) is False
    assert identity.discovery_item_identity(auction) is False


@pytest.mark.parametrize('categories,variant,accepted',[
    (('recipe',),DECOR,False), (('toy',),MOUNT,False), (('mount',),DECOR,False),
    (('mount',),MOUNT,True), (('decor',),DECOR,True), (('recipe',),None,True),
    (('battle-pet',),None,False), (('unknown',),None,False), ((),None,False),
])
def test_discovery_only_spends_metadata_budget_on_enabled_ordinary_categories(categories,variant,accepted):
    assert identity.discovery_item_identity(replace(A,variant_key=variant,category='unknown'),categories=categories) is accepted
