def __getattr__(name):
    import importlib
    return importlib.import_module(f'pkg.{name}')
