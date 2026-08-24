from django.shortcuts import render, redirect
from django.contrib import messages
from .models import User, Contact
from django.db.models import Q
from django.contrib.auth.hashers import make_password, check_password
import re
import random
from django.core.mail import send_mail
from django.template.loader import render_to_string


def get_current_user(request):
    user_id = request.session.get("user_id")
    if user_id:
        return User.objects.filter(id=user_id).first()


def home(request):
    user = get_current_user(request)

    if not user:
        return redirect("login")

    contacts = user.contacts.all()

    q = request.GET.get("q")
    if q:
        contacts = contacts.filter(Q(name__icontains=q) | Q(phone_number__icontains=q))

    return render(
        request,
        "home.html",
        {
            "user": user,
            "contacts": contacts,
        },
    )


def signup(request):
    if get_current_user(request):
        return redirect("home")

    if request.method == "POST":
        username = request.POST.get("username")
        email = request.POST.get("email")
        password = request.POST.get("password")
        password_confirm = request.POST.get("password_confirm")

        if not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email):
            messages.error(request, "Invalid email format!")
            return redirect("signup")

        if not re.match(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*#?&]).{8,}$", password
        ):
            messages.error(
                request,
                "Password must be at least 8 characters long, contain at least one uppercase letter, one lowercase letter, one number, and one special character.",
            )
            return redirect("signup")

        if password != password_confirm:
            messages.error(request, "Passwords do not match!")
            return redirect("signup")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already taken!")
            return redirect("signup")

        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already registered!")
            return redirect("signup")

        new_user = User(
            username=username, email=email, password=make_password(password)
        )
        new_user.save()

        request.session["user_id"] = new_user.id

        messages.success(request, "Account created successfully!")
        return redirect("home")

    return render(request, "signup.html")


def login(request):
    if get_current_user(request):
        return redirect("home")

    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")

        user = User.objects.filter(email=email).first()

        if user:
            if check_password(password, user.password):
                request.session["user_id"] = user.id

                messages.success(request, "Successfully logged in!")
                return redirect("home")
            else:
                messages.error(request, "Invalid password!")
        else:
            messages.error(request, "Invalid email!")
            return redirect("login")

    return render(request, "login.html")


def logout(request):
    request.session.flush()
    messages.success(request, "Successfully logged out!")
    return redirect("login")


def delete_account(request):
    user = get_current_user(request)

    if not user:
        messages.error(request, "You must be logged in to delete your account.")
        return redirect("login")

    if request.method == "POST":
        password = request.POST.get("password")
        if not check_password(password, user.password):
            messages.error(request, "Incorrect password. Account deletion failed.")
            return redirect("delete_account")

        otp = generate_otp()
        request.session["otp_code"] = otp
        request.session["otp_action"] = "delete"

        send_otp_email(user.email, otp)
        messages.success(
            request,
            "An OTP has been sent to your email to confirm the account deletion.",
        )
        return redirect("verify_otp")

    return render(request, "delete_account.html", {"user": user})


def generate_otp():
    return str(random.randint(100000, 999999))


def send_otp_email(email, otp):
    send_mail(
        "Your Verification Code",
        f"Your OTP is {otp}",
        "noreply@contactbook.com",
        [email],
        html_message=render_to_string("otp_email.html", {"otp": otp}),
        fail_silently=False,
    )


def change_password(request):
    user = get_current_user(request)

    if not user:
        messages.error(request, "You must be logged in to change your password.")
        return redirect("login")

    if request.method == "POST":
        current_password = request.POST.get("current_password")
        new_password = request.POST.get("new_password")
        confirm_password = request.POST.get("confirm_password")

        if not check_password(current_password, user.password):
            messages.error(request, "Current password is incorrect!")
            return redirect("change_password")

        if not re.match(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*#?&]).{8,}$", new_password
        ):
            messages.error(
                request,
                "Password must be at least 8 characters long, contain at least one uppercase letter, one lowercase letter, one number, and one special character.",
            )
            return redirect("change_password")

        if new_password != confirm_password:
            messages.error(request, "New passwords do not match!")
            return redirect("change_password")

        if current_password == new_password:
            messages.error(
                request, "New password cannot be the same as the old password!"
            )
            return redirect("change_password")

        otp = generate_otp()
        request.session["otp_code"] = otp
        request.session["change_pwd_new"] = new_password
        request.session["otp_action"] = "change"

        send_otp_email(user.email, otp)
        messages.success(
            request,
            "An OTP has been sent to your email to confirm the password change.",
        )
        return redirect("verify_otp")

    return render(request, "change_password.html", {"user": user})


def update_profile(request):
    user = get_current_user(request)

    if not user:
        messages.error(request, "You must be logged in to update your profile.")
        return redirect("login")

    if request.method == "POST":
        new_username = request.POST.get("username")
        password = request.POST.get("password")

        if not check_password(password, user.password):
            messages.error(request, "Incorrect password. Profile update failed.")
            return redirect("update_profile")

        if (
            new_username != user.username
            and User.objects.filter(username=new_username).exists()
        ):
            messages.error(request, "Username already taken!")
            return redirect("update_profile")

        user.username = new_username
        user.save()

        messages.success(request, "Profile updated successfully!")
        return redirect("home")

    return render(request, "update_profile.html", {"user": user})


def add_contact(request):
    user = get_current_user(request)
    if not user:
        messages.error(request, "You must be logged in to add a contact.")
        return redirect("login")

    if request.method == "POST":
        name = request.POST.get("name")
        phone_number = request.POST.get("phone_number")

        if not re.match(r"^\+?1?\d{9,15}$", phone_number):
            messages.error(request, "Invalid phone number format!")
            return redirect("add_contact")

        Contact.objects.create(user=user, name=name, phone_number=phone_number)
        messages.success(request, "Contact added successfully!")
        return redirect("home")

    return render(request, "add_contact.html")


def edit_contact(request, contact_id):
    user = get_current_user(request)
    if not user:
        messages.error(request, "You must be logged in to edit a contact.")
        return redirect("login")

    contact = Contact.objects.filter(id=contact_id, user=user).first()
    if not contact:
        messages.error(request, "Contact not found or access denied.")
        return redirect("home")

    if request.method == "POST":
        name = request.POST.get("name")
        phone_number = request.POST.get("phone_number")

        if not re.match(r"^\+?1?\d{9,15}$", phone_number):
            messages.error(request, "Invalid phone number format!")
            return redirect("edit_contact", contact_id=contact.id)

        contact.name = name
        contact.phone_number = phone_number
        contact.save()
        messages.success(request, "Contact updated successfully!")
        return redirect("home")

    return render(request, "edit_contact.html", {"contact": contact})


def delete_contact(request, contact_id):
    user = get_current_user(request)
    if not user:
        messages.error(request, "You must be logged in to delete a contact.")
        return redirect("login")

    contact = Contact.objects.filter(id=contact_id, user=user).first()
    if contact:
        contact.delete()
        messages.success(request, "Contact deleted successfully!")
    else:
        messages.error(request, "Contact not found or access denied.")

    return redirect("home")


def forgot_password(request):
    if request.method == "POST":
        email = request.POST.get("email")
        user = User.objects.filter(email=email).first()
        if user:
            otp = generate_otp()
            request.session["otp_code"] = otp
            request.session["reset_email"] = email
            request.session["otp_action"] = "forgot"
            send_otp_email(email, otp)
            messages.success(request, "An OTP has been sent to your email.")
            return redirect("verify_otp")
        else:
            messages.error(request, "No user found with this email.")
    return render(request, "forgot_password.html")


def resend_otp(request):
    action = request.session.get("otp_action")
    email = None

    if action == "forgot":
        email = request.session.get("reset_email")
    elif action in ["change", "delete"]:
        user = get_current_user(request)
        if user:
            email = user.email

    if email:
        otp = generate_otp()
        request.session["otp_code"] = otp
        send_otp_email(email, otp)
        messages.success(request, "A new OTP has been sent to your email.")
    else:
        messages.error(request, "Unable to resend OTP. Please try again.")

    return redirect("verify_otp")


def verify_otp(request):
    if request.method == "POST":
        otp = request.POST.get("otp")
        if otp == request.session.get("otp_code"):
            action = request.session.get("otp_action")
            if action == "forgot":
                request.session["otp_verified"] = True
                return redirect("reset_password")
            elif action == "change":
                user = get_current_user(request)
                if user:
                    new_password = request.session.get("change_pwd_new")
                    user.password = make_password(new_password)
                    user.save()
                    request.session.flush()
                    messages.success(
                        request, "Password changed successfully! Please log in again."
                    )
                    return redirect("login")
            elif action == "delete":
                user = get_current_user(request)
                if user:
                    Contact.objects.filter(user=user).delete()
                    user.delete()
                    request.session.flush()
                    messages.success(
                        request,
                        "Your account and all associated data have been deleted successfully.",
                    )
                    return redirect("login")
        else:
            messages.error(request, "Invalid OTP!")
    return render(request, "verify_otp.html")


def reset_password(request):
    if not request.session.get("otp_verified"):
        return redirect("login")

    if request.method == "POST":
        new_password = request.POST.get("new_password")
        confirm_password = request.POST.get("confirm_password")

        if not re.match(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*#?&]).{8,}$", new_password
        ):
            messages.error(
                request,
                "Password must be at least 8 characters long, contain at least one uppercase letter, one lowercase letter, one number, and one special character.",
            )
            return redirect("reset_password")

        if new_password != confirm_password:
            messages.error(request, "New passwords do not match!")
            return redirect("reset_password")

        email = request.session.get("reset_email")
        user = User.objects.filter(email=email).first()
        if user:
            user.password = make_password(new_password)
            user.save()
            request.session.flush()
            messages.success(request, "Password reset successfully! Please log in.")
            return redirect("login")

    return render(request, "reset_password.html")
