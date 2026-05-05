"""Constantes de configuração compartilhadas entre overlay_app.py e overlay_widgets.py."""

SETTING_DEFAULTS = {
    'restoreLastPosition':  True,
    'autoHideFound':        True,
    'autoHideLeftSidebar':  False,
    'autoHideRightSidebar': False,
    'transparency':         0,      # 0–90 %
    'roundWindow':          False,
    'followGameWindow':     False,
    'alwaysShowTitleBar':   False,
    'headingSource':        'auto', # 'auto'|'entity'|'delta'
    'rotateWithPlayer':     False,
    'rotateWithCamera':     False,
    'centerTeleportY':       1000.0,
    'disableGpuVsync':       False,
    'realtimeTransport':     'websocket', # 'native'|'websocket'
    'teleportEnabled':       True,
    'useSharedMemoryEntity': True,
    'nearbyControlsEnabled': False,
    'nearbyThreshold':       0.005,
    'nearbyRespectMapVisibility': True,
    'mapIconScale':          1.0,
    'browserZoom':           100,    # 70–150 %
    'controllerMapNavigation': False,
}
