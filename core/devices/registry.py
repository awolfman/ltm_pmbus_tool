# core/devices/registry.py
"""Central device registry -- maps MFR_SPECIAL_ID masks to profiles."""

DEVICE_REGISTRY = {}

def register_device(id_mask, name, num_pages, module_path):
    """Called by each device module to register itself."""
    DEVICE_REGISTRY[id_mask] = {
        'name': name,
        'num_pages': num_pages,
        'module': module_path,
    }

def get_device_profile(special_id):
    """Return (name, num_pages, extras_dict) for a given MFR_SPECIAL_ID."""
    if not special_id:
        return None, 1, {}
    masked = special_id & 0xFFF0
    for id_mask, info in DEVICE_REGISTRY.items():
        if masked == (id_mask & 0xFFF0):
            mod = _load_module(info['module'])
            return (
                info['name'],
                info['num_pages'],
                {
                    'register_overrides': getattr(mod, 'REGISTER_OVERRIDES', {}),
                    'read_only_extra': getattr(mod, 'READ_ONLY_EXTRA', set()),
                    'global_cmds_extra': getattr(mod, 'GLOBAL_CMDS_EXTRA', set()),
                },
            )
    return None, 1, {}

def _load_module(module_path):
    import importlib
    return importlib.import_module(module_path)

def _auto_register():
    """Import all device modules so they self-register."""
    import importlib
    for mod_name in (
        'core.devices.ltm4673',
        'core.devices.ltm4677',
        'core.devices.ltm4678',
    ):
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            print(f"[registry] failed to load {mod_name}: {e}")

_auto_register()
