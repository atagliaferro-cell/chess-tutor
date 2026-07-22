from .models import UserProfile


# Makes the current user's profile available to every page template.
def account_profile(request):
    profile = None


    # Only logged-in users need a profile object in the navbar.
    if request.user.is_authenticated:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)

    return {'account_profile': profile}
