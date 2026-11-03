"""Convert legacy Pywel calibration to MapGenie's current dlc-v3 layer.

Fit from 6,603 matching public landmark IDs, using Mercator coordinates.
95th percentile fit residual: 3.58e-7 degrees. Snapshot: 2026-11-03.
Old dataset: CoderFX/crimson-desert-interactive-progress-map/data/markers/pywel.js
Current dataset: mapgenie.io/api/v1/maps/887/data
"""
import math

def current_pywel_coordinates(lng, lat):
    y = math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))
    new_lng = 0.7558736927649842 * lng - 0.000001040294288053037 * y - 0.055448341691005924
    new_y = -0.00000006983566238526977 * lng + 0.7558724450875606 * y + 0.3393575264495709
    new_lat = math.degrees(2 * math.atan(math.exp(math.radians(new_y))) - math.pi / 2)
    return new_lng, new_lat
