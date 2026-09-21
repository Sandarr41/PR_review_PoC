def create_user(name):
    profile = None
    if name:
        profile = {"name": name}
    return profile.get("name")
