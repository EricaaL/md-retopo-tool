"""SOURCE registry. Add a source by registering a module; no if/elif on names."""
import importlib

_REGISTRY = {}


def register(source_id):
    def deco(fn):
        _REGISTRY[source_id] = fn
        return fn
    return deco


def get_loader(source_id):
    if source_id not in _REGISTRY:
        raise ValueError(f"Unknown source '{source_id}'. Known: {sorted(_REGISTRY)}")
    return _REGISTRY[source_id]


def load_mesh(source_id, path):
    return get_loader(source_id)(path)


# Load phase: declare and register only.
importlib.import_module(f"{__name__}.md")
