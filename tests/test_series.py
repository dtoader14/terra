import math

import numpy as np
import pytest

import series
from series import Grid

G1, GM = Grid(1, 1, .5, .5), Grid(.5, .625, 0, 0)
G025, G01 = Grid(.25, .25, .125, .125), Grid(.1, .1, .05, .05)
NAN = float("nan")
YEARLY = [10.0, 10.4, 10.1, 10.9, 10.6, 11.2, 11.0, 11.7, 11.3, 11.9]      # 2000..2009


def monthly(first_year, yearly):
    months = [f"{first_year + i}-{m:02d}" for i in range(len(yearly)) for m in range(1, 13)]
    return months, [v for v in yearly for _ in range(12)]


@pytest.mark.parametrize("grid,count", [(G1, 40), (GM, 135), (G025, 722), (G01, 4465)])
def test_cells_in_romania(grid, count):
    assert len(series.cells_in(grid, series.ROMANIA)) == count


def test_cells_small_box_edges_and_clip():
    box = (23.2, 45.9, 23.9, 46.3)
    assert series.cells_in(GM, box) == [(46.0, 23.75)]
    assert series.cells_in(G1, box) == [(46.5, 23.5)]                      # no centre inside: the cell holding the box centre
    assert series.cell_of(G1, 43.9, 25.97) == (43.5, 25.5)                 # Giurgiu: centre south of Romania's box
    assert series.cells_in(G025, (23.125, 45.125, 23.375, 45.375)) == [
        (45.125, 23.125), (45.125, 23.375), (45.375, 23.125), (45.375, 23.375)]   # edges count; lat then lon order
    assert series.clip((19, 43, 21, 44)) == (20.2, 43.6, 21, 44)
    assert series.clip((10, 40, 12, 42)) is None


def test_average_weights_gaps_and_offsets():
    w44, w48 = math.cos(math.radians(44)), math.cos(math.radians(48))
    months, values, used = series.average({(44.0, 25.0): ("2000-01", np.array([10, NAN])), (48.0, 25.0): ("2000-01", np.array([20.0, 30.0]))})
    assert months == ["2000-01", "2000-02"] and used == [2, 1]
    assert values == pytest.approx([(10 * w44 + 20 * w48) / (w44 + w48), 30])
    assert series.average({(44.0, 25.0): ("2000-01", np.array([1.0, 2.0])), (44.0, 26.0): ("2000-02", np.array([4.0]))})[1] == pytest.approx([1, 3])
    assert series.average({(44.0, 25.0): ("2000-01", np.array([NAN]))})[1:] == ([None], [0])
    assert series.average({(44.0, 25.0): ("2000-01", np.array([]))}) == ([], [], [])
    assert series.average({}) == ([], [], [])


def test_window():
    m, v, u = ["2000-01", "2000-02", "2000-03"], [1.0, None, 3.0], [1, 0, 1]
    assert series.window(m, v, u, "2000-02", None) == (["2000-02", "2000-03"], [None, 3.0], [0, 1])
    assert series.window(m, v, u, None, "2000-01") == (["2000-01"], [1.0], [1])
    assert series.window(m, v, u, "2001-01", None) == ([], [], [])


def test_trend_hand_checked():
    months, values = monthly(2000, YEARLY)
    assert series.yearly(months + ["2010-01"], values + [99.0]) == [(2000 + i, pytest.approx(v)) for i, v in enumerate(YEARLY)]
    t, reason = series.trend(months + ["2010-01"], values + [99.0])        # 2010 is incomplete and ignored
    assert reason is None and t["years"] == 10 and t["basis"] == "yearly means of complete years"
    assert t["slope_per_decade"] == pytest.approx(1.95757576, rel=1e-6)
    assert t["ci95"] == pytest.approx([1.305639, 2.609513], rel=1e-5)
    assert t["p_value"] == pytest.approx(1.215219e-04, rel=1e-4)
    assert t["mann_kendall_p"] == pytest.approx(1.282184e-03, rel=1e-5)
    assert t["significant"] is True
    assert t["line"] == [[2000, pytest.approx(10.029091, rel=1e-6)], [2009, pytest.approx(11.790909, rel=1e-6)]]


def test_trend_needs_ten_complete_years():
    months, values = monthly(2000, YEARLY[:9])
    assert series.trend(months, values) == (None, "Fewer than 10 complete years in this period.")
    months, values = monthly(2000, YEARLY)
    values[5] = None                                                       # a gap makes 2000 incomplete
    assert series.trend(months, values)[0] is None
    assert series.trend([], []) == (None, "Fewer than 10 complete years in this period.")


def test_mann_kendall():
    assert series.mann_kendall(list(range(20))) == pytest.approx(8.679762763e-10, rel=1e-6)
    assert series.mann_kendall([1, 1, 2, 2, 3, 3, 4, 4, 5, 5]) == pytest.approx(3.7057721e-4, rel=1e-6)   # ties
    assert series.mann_kendall([3, 3, 3, 3]) == 1.0


def test_variable_list():
    assert len(series.VARIABLES) == 354
    dew = series.VARIABLES["power:T2MDEW"]
    assert dew["name"] == "Dew/Frost Point at 2 Meters" and dew["kind"] == "model" and dew["grid"] == GM
    air = series.VARIABLES["gio:AIRS3STM_7_0_SurfAirTemp_A"]
    assert air["kind"] == "satellite" and air["grid"] == G1 and air["unit"] == "K" and air["begin"] == "2002-09-01"
    assert series.VARIABLES["power:ALLSKY_SFC_SW_DWN"]["kind"] == "satellite"
    assert series.VARIABLES["gio:GPM_3IMERGM_07_precipitation"]["grid"] == G01
    public = series.public_variable(dew)
    assert public["grid"] == "0.5 x 0.625 degree" and "begin" not in public and series.grid_label(G1) == "1 x 1 degree"


def test_places():
    for q in ("alba-iulia", "Alba Iulia", "ALBA IULIA", "alba iu"):
        assert series.find_places(q)[0]["name"] == "Alba Iulia"
    alba = series.find_places("Alba Iulia")[0]
    assert alba["county"] == "Alba" and abs(alba["lat"] - 46.07) < 0.1 and abs(alba["lon"] - 23.58) < 0.1
    assert set(alba) == {"id", "kind", "name", "county", "lat", "lon", "population", "bbox"} and alba["kind"] == "town"
    assert alba["bbox"] == [round(alba["lon"] - .25, 4), round(alba["lat"] - .25, 4), round(alba["lon"] + .25, 4), round(alba["lat"] + .25, 4)]
    assert series.find_places("pitesti")[0]["name"] in ("Pitești", "Piteşti")          # comma-below or cedilla, as GeoNames spells it
    assert len({p["county"] for p in series.find_places("mihai viteazu", 25)}) > 1   # villages sharing a name
    assert series.place(alba["id"]) == alba and series.place("nope") is None


def test_odd_place_text():
    assert series.find_places("") == [] and series.find_places("   ") == [] and series.find_places("x" * 500) == []
    assert len(series.find_places("a", 3)) == 3
    assert len(series.find_places("a", 0)) == 1 and len(series.find_places("a", 999)) == 25
    assert series.find_places("zzzzqq") == []


def test_trend_of_a_series_that_never_changes():
    months, values = monthly(2000, [5.0] * 12)                             # sea-ice fraction in Romania is 0 every year
    assert series.trend(months, values) == (None, "The yearly means do not change in this period.")


def test_counties_are_found_with_a_box_around_their_towns():
    first = series.find_places("cluj")[0]
    assert (first["kind"], first["id"], first["name"], first["county"]) == ("county", "county:Cluj", "Cluj", "Cluj")
    west, south, east, north = first["bbox"]
    seat = next(place for place in series.find_places("Cluj-Napoca") if place["kind"] == "town")
    assert west <= seat["lon"] <= east and south <= seat["lat"] <= north and 1 < (east - west) * (north - south) < 2
    assert series.place("county:Cluj") == first and series.place("county:Nowhere") is None
    assert series.find_places("brasov")[0]["id"] == "county:Brașov"             # found without its diacritics
    assert len(series.COUNTIES) == 41 and set(first) == set(seat)


def test_bucharest_is_offered_as_a_city_not_as_a_county():
    # The place list files three entries under "Bucharest"; a box around them is a few kilometres wide
    # with the city centre at its corner, and it used to be the first answer for the capital.
    assert series.place("county:Bucharest") is None
    assert len(series.COUNTIES) == 41
    first = series.find_places("bucharest")[0]
    assert (first["kind"], first["name"]) == ("town", "Bucharest")

