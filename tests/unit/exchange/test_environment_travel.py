"""四种固定方式共享天气和精确整数计费的回归。"""
import pytest
from worldmock.exchange.operators import evaluate_travel_fare, travel_seconds, delivery_seconds

@pytest.mark.parametrize('mode,speed,fare', [('walking',1.2,0),('driving',7,500),('taxi',7,1400),('bus',5,200)])
def test_four_modes_use_current_fixed_speed_weather_and_fare(mode,speed,fare):
    from math import ceil
    assert travel_seconds(mode,1000,0.85)==ceil(1000/(speed*0.85))
    assert evaluate_travel_fare(mode,1000)==fare

@pytest.mark.parametrize('mode,distance,fare',[('driving',5001,1001),('taxi',5001,1501),('bus',99999,200)])
def test_distance_fares_round_up_without_weather_or_float_loss(mode,distance,fare):
    assert evaluate_travel_fare(mode,distance)==fare

def test_delivery_does_not_read_travel_weather():
    assert delivery_seconds(1000)==800

@pytest.mark.parametrize('mode,weather',[('walking',0),('walking',-1),('walking',True),('boat',1)])
def test_invalid_modes_and_weather_are_rejected(mode,weather):
    with pytest.raises(ValueError):
        travel_seconds(mode,1000,weather)
