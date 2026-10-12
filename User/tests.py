"""
In-depth test suite for the Contacts app
(custom session-based User auth, OTP flows, per-user Contacts).

Place this file next to models.py / views.py / urls.py in your app folder
(rename it to tests.py if you like) and run:

    python manage.py test <your_app_name> -v 2

Covers:
    * Models        - uniqueness, cascade delete, related_name
    * URLs          - reverse() + resolve() for every named route
    * Helpers       - get_current_user, generate_otp, send_otp_email
    * Signup/Login  - validation rules, session handling, logout
    * Home          - auth gate, per-user isolation, search
    * Contacts      - add / edit / delete incl. phone validation + ownership
    * Profile       - update username, change password (OTP)
    * Delete account- password check + OTP confirmation
    * Forgot pwd    - OTP, resend, verify, reset
    * Workflows     - end-to-end scenarios for each OTP flow
    * Known issues  - @expectedFailure tests documenting bugs found in views.py
"""
from unittest import expectedFailure

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.messages import get_messages
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from . import views
from .models import Contact, User

STRONG = "Str0ng@Pass"
NEW_STRONG = "N3w#Passw0rd"
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
@override_settings(PASSWORD_HASHERS=FAST_HASHERS)  # keeps the suite fast
class BaseTestCase(TestCase):
    # ---- factories ---------------------------------------------------------
    def make_user(self, username="alice", email=None, password=STRONG):
        return User.objects.create(
            username=username,
            email=email or f"{username}@example.com",
            password=make_password(password),
        )

    def make_contact(self, user, name="Bob", phone="9876543210"):
        return Contact.objects.create(user=user, name=name, phone_number=phone)

    # ---- session helpers ---------------------------------------------------
    def login_as(self, user):
        session = self.client.session
        session["user_id"] = user.id
        session.save()

    def set_session(self, **values):
        session = self.client.session
        for key, value in values.items():
            session[key] = value
        session.save()

    def session_otp(self):
        return self.client.session["otp_code"]

    # ---- message helpers ---------------------------------------------------
    def flash_messages(self, response):
        return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]

    def assertFlash(self, response, level, fragment):
        msgs = self.flash_messages(response)
        self.assertTrue(
            any(lvl == level and fragment in text for lvl, text in msgs),
            f"Expected {level!r} message containing {fragment!r}, got {msgs}",
        )

    def assertRedirectsTo(self, response, name, **kwargs):
        self.assertRedirects(
            response, reverse(name, kwargs=kwargs or None), fetch_redirect_response=False
        )


# --------------------------------------------------------------------------- #
# MODEL TESTS
# --------------------------------------------------------------------------- #
class ModelTests(BaseTestCase):
    def test_username_unique(self):
        self.make_user("dup")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_user("dup", email="other@example.com")

    def test_email_unique(self):
        self.make_user("a", email="same@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_user("b", email="same@example.com")

    def test_contact_related_name(self):
        user = self.make_user()
        contact = self.make_contact(user)
        self.assertIn(contact, user.contacts.all())

    def test_deleting_user_cascades_to_contacts(self):
        user = self.make_user()
        self.make_contact(user)
        self.make_contact(user, "Carl", "9111111111")
        user.delete()
        self.assertEqual(Contact.objects.count(), 0)

    def test_deleting_contact_keeps_user(self):
        user = self.make_user()
        contact = self.make_contact(user)
        contact.delete()
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_field_lengths(self):
        self.assertEqual(User._meta.get_field("username").max_length, 150)
        self.assertEqual(Contact._meta.get_field("phone_number").max_length, 15)

    def test_hashed_password_fits_in_column(self):
        self.assertLessEqual(
            len(make_password(STRONG)), User._meta.get_field("password").max_length
        )


# --------------------------------------------------------------------------- #
# URL TESTS
# --------------------------------------------------------------------------- #
class UrlTests(BaseTestCase):
    def test_named_routes_resolve_to_expected_views(self):
        cases = [
            ("home", {}, views.home),
            ("signup", {}, views.signup),
            ("login", {}, views.login),
            ("logout", {}, views.logout),
            ("delete_account", {}, views.delete_account),
            ("change_password", {}, views.change_password),
            ("update_profile", {}, views.update_profile),
            ("add_contact", {}, views.add_contact),
            ("edit_contact", {"contact_id": 1}, views.edit_contact),
            ("delete_contact", {"contact_id": 1}, views.delete_contact),
            ("forgot_password", {}, views.forgot_password),
            ("verify_otp", {}, views.verify_otp),
            ("resend_otp", {}, views.resend_otp),
            ("reset_password", {}, views.reset_password),
        ]
        for name, kwargs, view in cases:
            with self.subTest(route=name):
                self.assertIs(resolve(reverse(name, kwargs=kwargs)).func, view)

    def test_non_integer_contact_id_is_404(self):
        url = reverse("edit_contact", kwargs={"contact_id": 1}).replace("/1/", "/abc/")
        self.assertEqual(self.client.get(url).status_code, 404)


# --------------------------------------------------------------------------- #
# HELPER FUNCTION TESTS
# --------------------------------------------------------------------------- #
class HelperFunctionTests(BaseTestCase):
    def _request(self, **session):
        request = RequestFactory().get("/")
        request.session = session
        return request

    def test_get_current_user(self):
        user = self.make_user()
        self.assertEqual(views.get_current_user(self._request(user_id=user.id)), user)
        self.assertIsNone(views.get_current_user(self._request()))
        self.assertIsNone(views.get_current_user(self._request(user_id=99999)))

    def test_generate_otp_is_six_digit_string(self):
        for _ in range(300):
            otp = views.generate_otp()
            self.assertIsInstance(otp, str)
            self.assertEqual(len(otp), 6)
            self.assertTrue(otp.isdigit())
            self.assertTrue(100000 <= int(otp) <= 999999)

    def test_send_otp_email(self):
        views.send_otp_email("someone@example.com", "123456")
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, "Your Verification Code")
        self.assertEqual(msg.to, ["someone@example.com"])
        self.assertIn("123456", msg.body)
        self.assertEqual(len(msg.alternatives), 1)  # html version attached


# --------------------------------------------------------------------------- #
# SIGNUP
# --------------------------------------------------------------------------- #
class SignupTests(BaseTestCase):
    def payload(self, **overrides):
        data = {
            "username": "newuser",
            "email": "newuser@example.com",
            "password": STRONG,
            "password_confirm": STRONG,
        }
        data.update(overrides)
        return data

    def test_get_renders_form(self):
        resp = self.client.get(reverse("signup"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "signup.html")

    def test_logged_in_user_redirected_home(self):
        self.login_as(self.make_user())
        self.assertRedirectsTo(self.client.get(reverse("signup")), "home")

    def test_success_creates_hashed_user_and_logs_in(self):
        resp = self.client.post(reverse("signup"), self.payload())
        self.assertRedirectsTo(resp, "home")
        user = User.objects.get(username="newuser")
        self.assertEqual(user.email, "newuser@example.com")
        self.assertNotEqual(user.password, STRONG)
        self.assertTrue(check_password(STRONG, user.password))
        self.assertEqual(self.client.session["user_id"], user.id)
        self.assertFlash(resp, "success", "Account created successfully")

    def test_invalid_emails_rejected(self):
        for bad in ["plainaddress", "a@b", "@x.com", "a b@x.com"]:
            with self.subTest(email=bad):
                resp = self.client.post(reverse("signup"), self.payload(email=bad))
                self.assertRedirectsTo(resp, "signup")
                self.assertFlash(resp, "error", "Invalid email format")
        self.assertEqual(User.objects.count(), 0)

    def test_weak_passwords_rejected(self):
        weak = ["Sh0rt@", "nouppercase1@", "NOLOWERCASE1@", "NoDigits@@@", "NoSpecial123"]
        for pw in weak:
            with self.subTest(password=pw):
                resp = self.client.post(
                    reverse("signup"), self.payload(password=pw, password_confirm=pw)
                )
                self.assertRedirectsTo(resp, "signup")
                self.assertFlash(resp, "error", "Password must be at least 8 characters")
        self.assertEqual(User.objects.count(), 0)

    def test_password_mismatch(self):
        resp = self.client.post(reverse("signup"), self.payload(password_confirm="Other1@xx"))
        self.assertRedirectsTo(resp, "signup")
        self.assertFlash(resp, "error", "Passwords do not match")
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_username(self):
        self.make_user("newuser", email="first@example.com")
        resp = self.client.post(reverse("signup"), self.payload())
        self.assertRedirectsTo(resp, "signup")
        self.assertFlash(resp, "error", "Username already taken")
        self.assertEqual(User.objects.count(), 1)

    def test_duplicate_email(self):
        self.make_user("other", email="newuser@example.com")
        resp = self.client.post(reverse("signup"), self.payload())
        self.assertRedirectsTo(resp, "signup")
        self.assertFlash(resp, "error", "Email already registered")
        self.assertEqual(User.objects.count(), 1)

    def test_failed_signup_does_not_log_in(self):
        self.client.post(reverse("signup"), self.payload(email="bad"))
        self.assertNotIn("user_id", self.client.session)


# --------------------------------------------------------------------------- #
# LOGIN / LOGOUT
# --------------------------------------------------------------------------- #
class LoginLogoutTests(BaseTestCase):
    def test_get_renders_form(self):
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "login.html")

    def test_success(self):
        user = self.make_user()
        resp = self.client.post(reverse("login"), {"email": user.email, "password": STRONG})
        self.assertRedirectsTo(resp, "home")
        self.assertEqual(self.client.session["user_id"], user.id)
        self.assertFlash(resp, "success", "Successfully logged in")

    def test_login_uses_email_not_username(self):
        user = self.make_user()
        resp = self.client.post(reverse("login"), {"email": user.username, "password": STRONG})
        self.assertRedirectsTo(resp, "login")
        self.assertNotIn("user_id", self.client.session)

    def test_wrong_password_rerenders_form(self):
        user = self.make_user()
        resp = self.client.post(reverse("login"), {"email": user.email, "password": "wrong"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("user_id", self.client.session)
        self.assertFlash(resp, "error", "Invalid password")

    def test_unknown_email_redirects_back(self):
        resp = self.client.post(reverse("login"), {"email": "ghost@example.com", "password": STRONG})
        self.assertRedirectsTo(resp, "login")
        self.assertFlash(resp, "error", "Invalid email")

    def test_missing_fields_do_not_log_in(self):
        self.make_user()
        for data in ({}, {"email": "alice@example.com"}):
            with self.subTest(data=data):
                self.client.post(reverse("login"), data)
                self.assertNotIn("user_id", self.client.session)

    def test_already_logged_in_redirects_home(self):
        self.login_as(self.make_user())
        self.assertRedirectsTo(self.client.get(reverse("login")), "home")

    def test_logout_clears_entire_session(self):
        self.login_as(self.make_user())
        self.set_session(otp_code="123456", otp_action="delete")
        resp = self.client.get(reverse("logout"))
        self.assertRedirectsTo(resp, "login")
        for key in ("user_id", "otp_code", "otp_action"):
            self.assertNotIn(key, self.client.session)
        self.assertFlash(resp, "success", "Successfully logged out")

    def test_logout_when_anonymous_is_harmless(self):
        self.assertRedirectsTo(self.client.get(reverse("logout")), "login")

    def test_user_deleted_while_logged_in_is_treated_as_anonymous(self):
        user = self.make_user()
        self.login_as(user)
        user.delete()
        self.assertRedirectsTo(self.client.get(reverse("home")), "login")


# --------------------------------------------------------------------------- #
# ACCESS CONTROL
# --------------------------------------------------------------------------- #
class AccessControlTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.contact = self.make_contact(self.user)

    def protected_urls(self):
        return [
            reverse("home"),
            reverse("delete_account"),
            reverse("change_password"),
            reverse("update_profile"),
            reverse("add_contact"),
            reverse("edit_contact", args=[self.contact.id]),
            reverse("delete_contact", args=[self.contact.id]),
        ]

    def test_anonymous_redirected_to_login(self):
        for url in self.protected_urls():
            with self.subTest(url=url):
                self.assertRedirectsTo(self.client.get(url), "login")

    def test_anonymous_post_cannot_mutate(self):
        self.client.post(reverse("add_contact"), {"name": "X", "phone_number": "9876543210"})
        self.client.post(reverse("edit_contact", args=[self.contact.id]), {"name": "Hacked", "phone_number": "9876543210"})
        self.client.post(reverse("delete_contact", args=[self.contact.id]))
        self.client.post(reverse("update_profile"), {"username": "hacked", "password": STRONG})
        self.client.post(reverse("delete_account"), {"password": STRONG})
        self.client.post(reverse("change_password"), {
            "current_password": STRONG, "new_password": NEW_STRONG, "confirm_password": NEW_STRONG,
        })
        self.user.refresh_from_db()
        self.contact.refresh_from_db()
        self.assertEqual(self.user.username, "alice")
        self.assertEqual(self.contact.name, "Bob")
        self.assertEqual(Contact.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_password_requires_otp_verification(self):
        self.assertRedirectsTo(self.client.get(reverse("reset_password")), "login")
        resp = self.client.post(reverse("reset_password"), {
            "new_password": NEW_STRONG, "confirm_password": NEW_STRONG,
        })
        self.assertRedirectsTo(resp, "login")

    def test_public_routes_accessible_without_login(self):
        for name in ("login", "signup", "forgot_password", "verify_otp"):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


# --------------------------------------------------------------------------- #
# HOME + SEARCH
# --------------------------------------------------------------------------- #
class HomeTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.login_as(self.user)

    def test_home_lists_only_own_contacts(self):
        mine = self.make_contact(self.user, "Mine")
        other = self.make_user("other")
        self.make_contact(other, "Theirs")
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "home.html")
        self.assertEqual(resp.context["user"], self.user)
        self.assertEqual(list(resp.context["contacts"]), [mine])

    def test_search_by_name_case_insensitive(self):
        self.make_contact(self.user, "Alice Wonder", "9000000001")
        self.make_contact(self.user, "Bob", "9000000002")
        resp = self.client.get(reverse("home"), {"q": "ALICE"})
        self.assertEqual([c.name for c in resp.context["contacts"]], ["Alice Wonder"])

    def test_search_by_phone_fragment(self):
        self.make_contact(self.user, "A", "9111111111")
        self.make_contact(self.user, "B", "9222222222")
        resp = self.client.get(reverse("home"), {"q": "9222"})
        self.assertEqual([c.name for c in resp.context["contacts"]], ["B"])

    def test_search_does_not_leak_other_users_contacts(self):
        other = self.make_user("other")
        self.make_contact(other, "Secret", "9333333333")
        resp = self.client.get(reverse("home"), {"q": "Secret"})
        self.assertEqual(len(resp.context["contacts"]), 0)

    def test_empty_query_returns_all(self):
        self.make_contact(self.user, "A")
        self.make_contact(self.user, "B", "9000000002")
        resp = self.client.get(reverse("home"), {"q": ""})
        self.assertEqual(len(resp.context["contacts"]), 2)

    def test_no_contacts(self):
        self.assertEqual(len(self.client.get(reverse("home")).context["contacts"]), 0)


# --------------------------------------------------------------------------- #
# CONTACTS CRUD
# --------------------------------------------------------------------------- #
class AddContactTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.login_as(self.user)

    def test_get_renders_form(self):
        resp = self.client.get(reverse("add_contact"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "add_contact.html")

    def test_valid_numbers_are_accepted(self):
        for number in ["9876543210", "+919876543210", "+12025550123", "123456789", "1234567890123456"]:
            with self.subTest(number=number):
                Contact.objects.all().delete()
                resp = self.client.post(reverse("add_contact"), {"name": "N", "phone_number": number})
                self.assertRedirectsTo(resp, "home")
                self.assertEqual(Contact.objects.get().phone_number, number)

    def test_invalid_numbers_are_rejected(self):
        for number in ["12345678", "abcdefghij", "98765-43210", "+", "", "12345678901234567", "98765 43210"]:
            with self.subTest(number=number):
                resp = self.client.post(reverse("add_contact"), {"name": "N", "phone_number": number})
                self.assertRedirectsTo(resp, "add_contact")
                self.assertFlash(resp, "error", "Invalid phone number format")
        self.assertEqual(Contact.objects.count(), 0)

    def test_contact_belongs_to_current_user(self):
        resp = self.client.post(reverse("add_contact"), {"name": "Zed", "phone_number": "9876543210"})
        c = Contact.objects.get()
        self.assertEqual((c.user, c.name), (self.user, "Zed"))
        self.assertFlash(resp, "success", "Contact added successfully")

    def test_duplicate_numbers_for_same_user_are_allowed(self):
        for _ in range(2):
            self.client.post(reverse("add_contact"), {"name": "Dup", "phone_number": "9876543210"})
        self.assertEqual(Contact.objects.count(), 2)


class EditContactTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.other = self.make_user("other")
        self.contact = self.make_contact(self.user)
        self.login_as(self.user)

    def test_get_renders_form(self):
        resp = self.client.get(reverse("edit_contact", args=[self.contact.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "edit_contact.html")
        self.assertEqual(resp.context["contact"], self.contact)

    def test_post_updates_contact(self):
        resp = self.client.post(
            reverse("edit_contact", args=[self.contact.id]),
            {"name": "Renamed", "phone_number": "+919999999999"},
        )
        self.assertRedirectsTo(resp, "home")
        self.contact.refresh_from_db()
        self.assertEqual((self.contact.name, self.contact.phone_number), ("Renamed", "+919999999999"))
        self.assertEqual(self.contact.user, self.user)
        self.assertFlash(resp, "success", "Contact updated successfully")

    def test_invalid_phone_rejected_and_nothing_saved(self):
        resp = self.client.post(
            reverse("edit_contact", args=[self.contact.id]),
            {"name": "Changed", "phone_number": "abc"},
        )
        self.assertRedirectsTo(resp, "edit_contact", contact_id=self.contact.id)
        self.assertFlash(resp, "error", "Invalid phone number format")
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.name, "Bob")

    def test_cannot_edit_another_users_contact(self):
        theirs = self.make_contact(self.other, "Theirs")
        for send in (
            lambda: self.client.get(reverse("edit_contact", args=[theirs.id])),
            lambda: self.client.post(
                reverse("edit_contact", args=[theirs.id]),
                {"name": "Hacked", "phone_number": "9876543210"},
            ),
        ):
            resp = send()
            self.assertRedirectsTo(resp, "home")
            self.assertFlash(resp, "error", "Contact not found or access denied")
        theirs.refresh_from_db()
        self.assertEqual(theirs.name, "Theirs")

    def test_unknown_contact(self):
        resp = self.client.get(reverse("edit_contact", args=[99999]))
        self.assertRedirectsTo(resp, "home")
        self.assertFlash(resp, "error", "Contact not found or access denied")


class DeleteContactTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.other = self.make_user("other")
        self.contact = self.make_contact(self.user)
        self.login_as(self.user)

    def test_post_deletes_own_contact(self):
        resp = self.client.post(reverse("delete_contact", args=[self.contact.id]))
        self.assertRedirectsTo(resp, "home")
        self.assertFalse(Contact.objects.filter(pk=self.contact.pk).exists())
        self.assertFlash(resp, "success", "Contact deleted successfully")

    def test_cannot_delete_another_users_contact(self):
        theirs = self.make_contact(self.other, "Theirs")
        resp = self.client.post(reverse("delete_contact", args=[theirs.id]))
        self.assertRedirectsTo(resp, "home")
        self.assertFlash(resp, "error", "Contact not found or access denied")
        self.assertTrue(Contact.objects.filter(pk=theirs.pk).exists())

    def test_unknown_contact(self):
        resp = self.client.post(reverse("delete_contact", args=[99999]))
        self.assertRedirectsTo(resp, "home")
        self.assertFlash(resp, "error", "Contact not found or access denied")

    def test_only_target_contact_is_removed(self):
        keep = self.make_contact(self.user, "Keep", "9000000009")
        self.client.post(reverse("delete_contact", args=[self.contact.id]))
        self.assertEqual(list(Contact.objects.all()), [keep])


# --------------------------------------------------------------------------- #
# UPDATE PROFILE
# --------------------------------------------------------------------------- #
class UpdateProfileTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.login_as(self.user)

    def test_get_renders_form(self):
        resp = self.client.get(reverse("update_profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "update_profile.html")

    def test_success(self):
        resp = self.client.post(reverse("update_profile"), {"username": "renamed", "password": STRONG})
        self.assertRedirectsTo(resp, "home")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "renamed")
        self.assertFlash(resp, "success", "Profile updated successfully")

    def test_wrong_password_blocks_update(self):
        resp = self.client.post(reverse("update_profile"), {"username": "renamed", "password": "nope"})
        self.assertRedirectsTo(resp, "update_profile")
        self.assertFlash(resp, "error", "Incorrect password")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice")

    def test_missing_password_blocks_update(self):
        self.client.post(reverse("update_profile"), {"username": "renamed"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice")

    def test_username_taken(self):
        self.make_user("taken")
        resp = self.client.post(reverse("update_profile"), {"username": "taken", "password": STRONG})
        self.assertRedirectsTo(resp, "update_profile")
        self.assertFlash(resp, "error", "Username already taken")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice")

    def test_keeping_same_username_is_allowed(self):
        resp = self.client.post(reverse("update_profile"), {"username": "alice", "password": STRONG})
        self.assertRedirectsTo(resp, "home")
        self.assertFlash(resp, "success", "Profile updated successfully")

    def test_email_and_password_are_untouched(self):
        old_hash = self.user.password
        self.client.post(reverse("update_profile"), {"username": "renamed", "password": STRONG})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "alice@example.com")
        self.assertEqual(self.user.password, old_hash)


# --------------------------------------------------------------------------- #
# CHANGE PASSWORD (initiation; OTP verification is tested separately)
# --------------------------------------------------------------------------- #
class ChangePasswordTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.login_as(self.user)

    def payload(self, **overrides):
        data = {"current_password": STRONG, "new_password": NEW_STRONG, "confirm_password": NEW_STRONG}
        data.update(overrides)
        return data

    def test_get_renders_form(self):
        resp = self.client.get(reverse("change_password"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "change_password.html")

    def test_valid_request_sends_otp_and_defers_change(self):
        resp = self.client.post(reverse("change_password"), self.payload())
        self.assertRedirectsTo(resp, "verify_otp")
        self.assertEqual(self.client.session["otp_action"], "change")
        self.assertEqual(len(self.client.session["otp_code"]), 6)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn(self.client.session["otp_code"], mail.outbox[0].body)
        self.assertFlash(resp, "success", "OTP has been sent")
        # password must NOT change until OTP is verified
        self.user.refresh_from_db()
        self.assertTrue(check_password(STRONG, self.user.password))

    def test_wrong_current_password(self):
        resp = self.client.post(reverse("change_password"), self.payload(current_password="bad"))
        self.assertRedirectsTo(resp, "change_password")
        self.assertFlash(resp, "error", "Current password is incorrect")
        self.assertEqual(len(mail.outbox), 0)

    def test_weak_new_password(self):
        resp = self.client.post(
            reverse("change_password"), self.payload(new_password="weak", confirm_password="weak")
        )
        self.assertRedirectsTo(resp, "change_password")
        self.assertFlash(resp, "error", "Password must be at least 8 characters")
        self.assertEqual(len(mail.outbox), 0)

    def test_confirmation_mismatch(self):
        resp = self.client.post(reverse("change_password"), self.payload(confirm_password="Different1@"))
        self.assertRedirectsTo(resp, "change_password")
        self.assertFlash(resp, "error", "New passwords do not match")

    def test_new_password_same_as_old(self):
        resp = self.client.post(
            reverse("change_password"), self.payload(new_password=STRONG, confirm_password=STRONG)
        )
        self.assertRedirectsTo(resp, "change_password")
        self.assertFlash(resp, "error", "cannot be the same as the old password")

    def test_failed_validation_sets_no_otp_session_state(self):
        self.client.post(reverse("change_password"), self.payload(new_password="weak", confirm_password="weak"))
        for key in ("otp_code", "otp_action", "change_pwd_new"):
            self.assertNotIn(key, self.client.session)


# --------------------------------------------------------------------------- #
# DELETE ACCOUNT (initiation)
# --------------------------------------------------------------------------- #
class DeleteAccountTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.login_as(self.user)

    def test_get_renders_confirmation(self):
        resp = self.client.get(reverse("delete_account"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "delete_account.html")

    def test_wrong_password(self):
        resp = self.client.post(reverse("delete_account"), {"password": "nope"})
        self.assertRedirectsTo(resp, "delete_account")
        self.assertFlash(resp, "error", "Incorrect password")
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn("otp_code", self.client.session)

    def test_correct_password_sends_otp_but_does_not_delete_yet(self):
        resp = self.client.post(reverse("delete_account"), {"password": STRONG})
        self.assertRedirectsTo(resp, "verify_otp")
        self.assertEqual(self.client.session["otp_action"], "delete")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertFlash(resp, "success", "OTP has been sent")


# --------------------------------------------------------------------------- #
# FORGOT PASSWORD / RESEND / VERIFY / RESET
# --------------------------------------------------------------------------- #
class ForgotPasswordTests(BaseTestCase):
    def test_get_renders_form(self):
        resp = self.client.get(reverse("forgot_password"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forgot_password.html")

    def test_known_email_sends_otp(self):
        user = self.make_user()
        resp = self.client.post(reverse("forgot_password"), {"email": user.email})
        self.assertRedirectsTo(resp, "verify_otp")
        session = self.client.session
        self.assertEqual(session["otp_action"], "forgot")
        self.assertEqual(session["reset_email"], user.email)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn(session["otp_code"], mail.outbox[0].body)
        self.assertFlash(resp, "success", "OTP has been sent")

    def test_unknown_email_shows_error_and_sends_nothing(self):
        resp = self.client.post(reverse("forgot_password"), {"email": "ghost@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertFlash(resp, "error", "No user found with this email")
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn("otp_code", self.client.session)

    def test_missing_email(self):
        resp = self.client.post(reverse("forgot_password"), {})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_does_not_change_password(self):
        user = self.make_user()
        self.client.post(reverse("forgot_password"), {"email": user.email})
        user.refresh_from_db()
        self.assertTrue(check_password(STRONG, user.password))


class ResendOtpTests(BaseTestCase):
    def test_resend_for_forgot_flow(self):
        user = self.make_user()
        self.set_session(otp_action="forgot", reset_email=user.email, otp_code="000000")
        resp = self.client.get(reverse("resend_otp"))
        self.assertRedirectsTo(resp, "verify_otp")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn(self.session_otp(), mail.outbox[0].body)
        self.assertFlash(resp, "success", "new OTP has been sent")

    def test_resend_for_change_and_delete_flows_uses_logged_in_email(self):
        user = self.make_user()
        self.login_as(user)
        for action in ("change", "delete"):
            with self.subTest(action=action):
                mail.outbox.clear()
                self.set_session(otp_action=action)
                resp = self.client.get(reverse("resend_otp"))
                self.assertRedirectsTo(resp, "verify_otp")
                self.assertEqual(mail.outbox[0].to, [user.email])

    def test_resend_for_change_flow_when_not_logged_in_fails(self):
        self.set_session(otp_action="change")
        resp = self.client.get(reverse("resend_otp"))
        self.assertRedirectsTo(resp, "verify_otp")
        self.assertFlash(resp, "error", "Unable to resend OTP")
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_without_any_pending_action_fails(self):
        resp = self.client.get(reverse("resend_otp"))
        self.assertRedirectsTo(resp, "verify_otp")
        self.assertFlash(resp, "error", "Unable to resend OTP")
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_keeps_the_same_action(self):
        user = self.make_user()
        self.set_session(otp_action="forgot", reset_email=user.email, otp_code="000000")
        self.client.get(reverse("resend_otp"))
        self.assertEqual(self.client.session["otp_action"], "forgot")


class VerifyOtpTests(BaseTestCase):
    def test_get_renders_form(self):
        resp = self.client.get(reverse("verify_otp"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "verify_otp.html")

    def test_wrong_otp(self):
        self.set_session(otp_code="123456", otp_action="forgot")
        resp = self.client.post(reverse("verify_otp"), {"otp": "654321"})
        self.assertEqual(resp.status_code, 200)
        self.assertFlash(resp, "error", "Invalid OTP")
        self.assertNotIn("otp_verified", self.client.session)

    def test_otp_with_whitespace_is_rejected(self):
        self.set_session(otp_code="123456", otp_action="forgot")
        resp = self.client.post(reverse("verify_otp"), {"otp": " 123456"})
        self.assertFlash(resp, "error", "Invalid OTP")

    def test_forgot_flow_marks_session_verified(self):
        self.set_session(otp_code="123456", otp_action="forgot", reset_email="a@b.com")
        resp = self.client.post(reverse("verify_otp"), {"otp": "123456"})
        self.assertRedirectsTo(resp, "reset_password")
        self.assertTrue(self.client.session["otp_verified"])

    def test_change_flow_updates_password_and_flushes_session(self):
        user = self.make_user()
        self.login_as(user)
        self.set_session(otp_code="123456", otp_action="change", change_pwd_new=NEW_STRONG)
        resp = self.client.post(reverse("verify_otp"), {"otp": "123456"})
        self.assertRedirectsTo(resp, "login")
        user.refresh_from_db()
        self.assertTrue(check_password(NEW_STRONG, user.password))
        self.assertFalse(check_password(STRONG, user.password))
        self.assertNotIn("user_id", self.client.session)
        self.assertNotIn("change_pwd_new", self.client.session)
        self.assertFlash(resp, "success", "Password changed successfully")

    def test_change_flow_without_login_does_nothing(self):
        user = self.make_user()
        self.set_session(otp_code="123456", otp_action="change", change_pwd_new=NEW_STRONG)
        resp = self.client.post(reverse("verify_otp"), {"otp": "123456"})
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(check_password(STRONG, user.password))

    def test_delete_flow_removes_user_and_contacts(self):
        user = self.make_user()
        other = self.make_user("other")
        self.make_contact(user)
        survivor = self.make_contact(other, "Keep")
        self.login_as(user)
        self.set_session(otp_code="123456", otp_action="delete")
        resp = self.client.post(reverse("verify_otp"), {"otp": "123456"})
        self.assertRedirectsTo(resp, "login")
        self.assertFalse(User.objects.filter(pk=user.pk).exists())
        self.assertEqual(list(Contact.objects.all()), [survivor])
        self.assertNotIn("user_id", self.client.session)
        self.assertFlash(resp, "success", "account and all associated data have been deleted")

    def test_delete_flow_without_login_does_nothing(self):
        user = self.make_user()
        self.set_session(otp_code="123456", otp_action="delete")
        resp = self.client.post(reverse("verify_otp"), {"otp": "123456"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_wrong_otp_does_not_change_or_delete_anything(self):
        user = self.make_user()
        self.login_as(user)
        self.set_session(otp_code="123456", otp_action="delete")
        self.client.post(reverse("verify_otp"), {"otp": "000000"})
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_no_pending_action_is_a_noop(self):
        user = self.make_user()
        self.login_as(user)
        resp = self.client.post(reverse("verify_otp"), {})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertNotIn("otp_verified", self.client.session)


class ResetPasswordTests(BaseTestCase):
    def setUp(self):
        self.user = self.make_user()
        self.set_session(otp_verified=True, reset_email=self.user.email)

    def test_get_renders_form_when_verified(self):
        resp = self.client.get(reverse("reset_password"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "reset_password.html")

    def test_success_updates_password_and_flushes_session(self):
        resp = self.client.post(reverse("reset_password"), {
            "new_password": NEW_STRONG, "confirm_password": NEW_STRONG,
        })
        self.assertRedirectsTo(resp, "login")
        self.user.refresh_from_db()
        self.assertTrue(check_password(NEW_STRONG, self.user.password))
        for key in ("otp_verified", "reset_email", "user_id"):
            self.assertNotIn(key, self.client.session)
        self.assertFlash(resp, "success", "Password reset successfully")

    def test_weak_password(self):
        resp = self.client.post(reverse("reset_password"), {"new_password": "weak", "confirm_password": "weak"})
        self.assertRedirectsTo(resp, "reset_password")
        self.assertFlash(resp, "error", "Password must be at least 8 characters")
        self.user.refresh_from_db()
        self.assertTrue(check_password(STRONG, self.user.password))

    def test_mismatch(self):
        resp = self.client.post(reverse("reset_password"), {
            "new_password": NEW_STRONG, "confirm_password": "Other1@xxx",
        })
        self.assertRedirectsTo(resp, "reset_password")
        self.assertFlash(resp, "error", "New passwords do not match")

    def test_unknown_reset_email_rerenders_form_without_crashing(self):
        self.set_session(reset_email="ghost@example.com")
        resp = self.client.post(reverse("reset_password"), {
            "new_password": NEW_STRONG, "confirm_password": NEW_STRONG,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "reset_password.html")

    def test_reset_does_not_log_user_in(self):
        self.client.post(reverse("reset_password"), {"new_password": NEW_STRONG, "confirm_password": NEW_STRONG})
        self.assertNotIn("user_id", self.client.session)


# --------------------------------------------------------------------------- #
# END-TO-END WORKFLOWS
# --------------------------------------------------------------------------- #
class WorkflowTests(BaseTestCase):
    def login_via_form(self, email, password):
        return self.client.post(reverse("login"), {"email": email, "password": password})

    def test_signup_contacts_and_logout(self):
        self.client.post(reverse("signup"), {
            "username": "flow", "email": "flow@example.com", "password": STRONG, "password_confirm": STRONG,
        })
        self.client.post(reverse("add_contact"), {"name": "Mum", "phone_number": "9876543210"})
        contact = Contact.objects.get()
        self.client.post(reverse("edit_contact", args=[contact.id]), {"name": "Mother", "phone_number": "9876543210"})
        self.assertEqual([c.name for c in self.client.get(reverse("home")).context["contacts"]], ["Mother"])
        self.client.post(reverse("delete_contact", args=[contact.id]))
        self.assertEqual(Contact.objects.count(), 0)
        self.client.get(reverse("logout"))
        self.assertRedirectsTo(self.client.get(reverse("home")), "login")

    def test_forgot_password_end_to_end(self):
        user = self.make_user()
        self.client.post(reverse("forgot_password"), {"email": user.email})
        otp = self.session_otp()
        self.client.post(reverse("verify_otp"), {"otp": otp})
        self.client.post(reverse("reset_password"), {"new_password": NEW_STRONG, "confirm_password": NEW_STRONG})
        self.client.get(reverse("logout"))

        self.login_via_form(user.email, STRONG)
        self.assertNotIn("user_id", self.client.session)  # old password dead
        resp = self.login_via_form(user.email, NEW_STRONG)
        self.assertRedirectsTo(resp, "home")

    def test_change_password_end_to_end_with_resend(self):
        user = self.make_user()
        self.login_as(user)
        self.client.post(reverse("change_password"), {
            "current_password": STRONG, "new_password": NEW_STRONG, "confirm_password": NEW_STRONG,
        })
        self.client.get(reverse("resend_otp"))
        self.assertEqual(len(mail.outbox), 2)
        latest_otp = self.session_otp()
        self.assertIn(latest_otp, mail.outbox[-1].body)
        resp = self.client.post(reverse("verify_otp"), {"otp": latest_otp})
        self.assertRedirectsTo(resp, "login")

        self.assertNotIn("user_id", self.client.session)
        self.login_via_form(user.email, STRONG)
        self.assertNotIn("user_id", self.client.session)
        self.assertRedirectsTo(self.login_via_form(user.email, NEW_STRONG), "home")

    def test_delete_account_end_to_end(self):
        user = self.make_user()
        self.make_contact(user)
        self.login_as(user)
        self.client.post(reverse("delete_account"), {"password": STRONG})
        self.client.post(reverse("verify_otp"), {"otp": self.session_otp()})
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)
        resp = self.login_via_form(user.email, STRONG)
        self.assertRedirectsTo(resp, "login")


# --------------------------------------------------------------------------- #
# KNOWN ISSUES
# Each test asserts the *desired* behaviour and is marked @expectedFailure
# because views.py does not do it today. When you fix the bug the test turns
# into an "unexpected success" - just delete the decorator.
# --------------------------------------------------------------------------- #
class KnownIssueTests(BaseTestCase):
    @expectedFailure
    def test_otp_verification_state_must_not_survive_a_new_forgot_request(self):
        """ACCOUNT TAKEOVER: otp_verified is never cleared. An attacker verifies
        an OTP for their OWN email, then submits the VICTIM's email to
        forgot_password (which swaps reset_email but leaves otp_verified=True),
        then calls reset_password directly - no OTP for the victim needed."""
        attacker = self.make_user("attacker")
        victim = self.make_user("victim")

        self.client.post(reverse("forgot_password"), {"email": attacker.email})
        self.client.post(reverse("verify_otp"), {"otp": self.session_otp()})
        self.client.post(reverse("forgot_password"), {"email": victim.email})
        self.client.post(reverse("reset_password"), {
            "new_password": NEW_STRONG, "confirm_password": NEW_STRONG,
        })

        victim.refresh_from_db()
        self.assertTrue(check_password(STRONG, victim.password))

    @expectedFailure
    def test_new_password_should_not_be_stored_in_plaintext_in_session(self):
        user = self.make_user()
        self.login_as(user)
        self.client.post(reverse("change_password"), {
            "current_password": STRONG, "new_password": NEW_STRONG, "confirm_password": NEW_STRONG,
        })
        self.assertNotEqual(self.client.session.get("change_pwd_new"), NEW_STRONG)

    @expectedFailure
    def test_otp_should_lock_after_repeated_wrong_attempts(self):
        """6-digit OTP with unlimited guesses can be brute-forced."""
        self.set_session(otp_code="123456", otp_action="forgot", reset_email="a@b.com")
        for _ in range(10):
            self.client.post(reverse("verify_otp"), {"otp": "000000"})
        resp = self.client.post(reverse("verify_otp"), {"otp": "123456"})
        self.assertEqual(resp.status_code, 200)  # should be refused, not redirected

    @expectedFailure
    def test_otp_should_be_single_use(self):
        """otp_code stays in the session after a successful verification."""
        self.set_session(otp_code="123456", otp_action="forgot", reset_email="a@b.com")
        self.client.post(reverse("verify_otp"), {"otp": "123456"})
        self.assertNotIn("otp_code", self.client.session)

    @expectedFailure
    def test_forgot_password_should_not_reveal_whether_an_email_is_registered(self):
        """Unknown emails get a different response (200 + error) than known ones (redirect)."""
        resp = self.client.post(reverse("forgot_password"), {"email": "ghost@example.com"})
        self.assertEqual(resp.status_code, 302)

    @expectedFailure
    def test_delete_contact_should_not_work_via_get(self):
        """State-changing action on GET (CSRF-able, prefetchable)."""
        user = self.make_user()
        contact = self.make_contact(user)
        self.login_as(user)
        self.client.get(reverse("delete_contact", args=[contact.id]))
        self.assertTrue(Contact.objects.filter(pk=contact.pk).exists())

    @expectedFailure
    def test_update_profile_should_reject_blank_username(self):
        user = self.make_user()
        self.login_as(user)
        self.client.post(reverse("update_profile"), {"username": "", "password": STRONG})
        user.refresh_from_db()
        self.assertEqual(user.username, "alice")

    @expectedFailure
    def test_phone_regex_should_not_allow_values_longer_than_the_column(self):
        """Regex allows up to 17 chars ('+1' + 15 digits) but phone_number is max_length=15."""
        user = self.make_user()
        self.login_as(user)
        self.client.post(reverse("add_contact"), {"name": "Long", "phone_number": "+1202555012345678"})
        self.assertFalse(Contact.objects.filter(name="Long").exists())
