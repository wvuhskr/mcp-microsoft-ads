from suds.sudsobject import Object, asdict


def suds_to_dict(obj):
    if isinstance(obj, Object):
        return {k: suds_to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [suds_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: suds_to_dict(v) for k, v in obj.items()}
    if hasattr(obj, "__dict__"):  # duck-typed suds stand-ins (e.g. SimpleNamespace in tests)
        return {k: suds_to_dict(v) for k, v in vars(obj).items()}
    return obj
