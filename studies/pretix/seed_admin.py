from pretix.base.models import User, Organizer, Team
u, _ = User.objects.get_or_create(email="study@example.com")
u.set_password("study-local-only"); u.is_staff = True; u.save()
o, _ = Organizer.objects.get_or_create(slug="study", defaults={"name": "Study Org"})
t, _ = Team.objects.get_or_create(organizer=o, name="Admins", defaults=dict(
    all_events=True, all_event_permissions=True, all_organizer_permissions=True))
t.members.add(u)
tok = t.tokens.create(name="study")
print("TOKEN=" + tok.token)
