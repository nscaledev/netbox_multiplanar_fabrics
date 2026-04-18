def select_scope_inputs(*, fabric=None, plane=None, site=None, location=None):
    if fabric is not None:
        site = site or getattr(fabric, 'scope_site', None)
        location = location or getattr(fabric, 'scope_location', None)

    return {
        'fabric': fabric,
        'plane': plane,
        'site': site,
        'location': location,
    }
