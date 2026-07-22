from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

# URL patterns connect browser paths to the correct view functions.
urlpatterns = [
    # Homepage explains the teaching purpose before users start playing.
    path('', views.home, name='home'),
    # Bot ladder routes handle the challenge progression section.
    path('play/', views.play_view, name='play'),
    # Puzzle routes provide category training and rated puzzle results.
    path('puzzles/', views.puzzles_view, name='puzzles'),
    # Dashboard route summarises progress from games, tutors and puzzles.
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('puzzles/<slug:category_slug>/rated-result/', views.puzzle_rated_result_view, name='puzzle_rated_result'),
    path('puzzles/<slug:category_slug>/', views.puzzle_detail_view, name='puzzle_detail'),
    # Tutor routes provide guided feedback rather than simple bot play.
    path('tutors/', views.tutors_view, name='tutors'),
    path('tutors/<slug:tutor_slug>/start/', views.tutor_start_view, name='tutor_start'),
    path('tutors/<slug:tutor_slug>/move/', views.tutor_move_view, name='tutor_move'),
    path('tutors/<slug:tutor_slug>/reply/', views.tutor_reply_view, name='tutor_reply'),
    path('tutors/<slug:tutor_slug>/undo/', views.tutor_undo_view, name='tutor_undo'),
    path('tutors/<slug:tutor_slug>/', views.tutor_game_view, name='tutor_game'),
    path('play/<slug:bot_slug>/', views.bot_theme_view, name='bot_theme'),
    path('play/<slug:bot_slug>/<slug:theme_slug>/start/', views.bot_start_view, name='bot_start'),
    path('play/<slug:bot_slug>/<slug:theme_slug>/move/', views.bot_move_view, name='bot_move'),
    path('play/<slug:bot_slug>/<slug:theme_slug>/reply/', views.bot_reply_view, name='bot_reply'),
    path('play/<slug:bot_slug>/<slug:theme_slug>/', views.bot_game_view, name='bot_game'),
    # Authentication routes control sign in, registration and account recovery.
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    # Settings lets the user update account details without leaving the app.
    path('settings/', views.account_settings_view, name='account_settings'),
    path('verification-sent/', views.verification_sent_view, name='verification_sent'),
    path('verify-email/<uidb64>/<token>/', views.verify_email_view, name='verify_email'),
    path('logout/', views.logout_view, name='logout'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='main/password_reset.html',
            email_template_name='main/emails/password_reset_email.txt',
            subject_template_name='main/emails/password_reset_subject.txt',
            success_url='/password-reset/done/',
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(template_name='main/password_reset_done.html'),
        name='password_reset_done',
    ),
    path(
        'password-reset-confirm/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='main/password_reset_confirm.html',
            success_url='/password-reset-complete/',
        ),
        name='password_reset_confirm',
    ),
    path(
        'password-reset-complete/',
        auth_views.PasswordResetCompleteView.as_view(template_name='main/password_reset_complete.html'),
        name='password_reset_complete',
    ),
]
