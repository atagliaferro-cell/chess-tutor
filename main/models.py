from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


# Extends the built-in Django user with Chess Tutor progress data.
class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    # Puzzle rating starts low so improvement is visible after training.
    puzzle_rating = models.PositiveIntegerField(default=100)
    # Profile pictures personalise the navbar and account settings page.
    avatar = models.FileField(
        upload_to='profile_pictures/',
        blank=True,
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
    )

    def __str__(self):
        return f'{self.user.username} profile'



# Records rated puzzle attempts so one puzzle cannot be repeated for unlimited ELO.
class PuzzleRatingAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='puzzle_rating_attempts')
    category_slug = models.SlugField(max_length=80)
    puzzle_index = models.PositiveSmallIntegerField()
    # Perfect is true only when the full puzzle line is solved without help.
    perfect = models.BooleanField(default=False)
    # Delta stores the exact rating change awarded for this attempt.
    delta = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'category_slug', 'puzzle_index')
        ordering = ['category_slug', 'puzzle_index']

    def __str__(self):
        return f'{self.user.username} {self.category_slug} puzzle {self.puzzle_index + 1}'



# This signal creates a matching profile automatically when a new account is made.
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
