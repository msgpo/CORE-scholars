from flask import flash, redirect, render_template, request, url_for, jsonify, current_app
from flask_login import (current_user, login_required, login_user,
                         logout_user)
from flask_rq import get_queue

from . import account
from .. import db, csrf
from ..email import send_email

from ..models import User, SavingsHistory, EditableHTML, PhoneNumberState, Stage, SiteAttributes
from .forms import (ChangeEmailForm, ChangePasswordForm, CreatePasswordForm,
                    LoginForm, RegistrationForm, RequestResetPasswordForm,
                    ResetPasswordForm, ProfileForm, SavingsStartEndForm, SavingsHistoryForm,
                    VerifyPhoneNumberForm)

from datetime import timedelta
import json
import os
import time
import boto3
import random


@account.route('/', methods=['GET', 'POST'])
@login_required
def index():
    str_format = lambda x: '{0:.2f}'.format(x)
    modules_left = current_user.modules.count(None)
    return render_template('account/index.html', str_format=str_format, modules_left=modules_left)


@account.route('/login', methods=['GET', 'POST'])
def login():
    """Log in an existing user."""
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user is not None and user.password_hash is not None and \
                user.verify_password(form.password.data):
            login_user(user, form.remember_me.data)
            flash('You are now logged in. Welcome back!', 'success')
            return redirect(request.args.get('next') or url_for('main.index'))
        else:
            flash('Invalid email or password.', 'form-error')
    return render_template('account/login.html', form=form)


@account.route('/register', methods=['GET', 'POST'])
def register():
    """Register a new user, and send them a confirmation email."""
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            password=form.password.data)
        db.session.add(user)
        db.session.commit()
        token = user.generate_confirmation_token()
        confirm_link = url_for('account.confirm', token=token, _external=True)
        get_queue().enqueue(
            send_email,
            recipient=user.email,
            subject='Confirm Your Account',
            template='account/email/confirm',
            user=user,
            confirm_link=confirm_link)
        flash('A confirmation link has been sent to {}.'.format(user.email),
              'warning')
        return redirect(url_for('main.index'))
    return render_template('account/register.html', form=form)


@account.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))


@account.route('/manage', methods=['GET', 'POST'])
@account.route('/manage/info', methods=['GET', 'POST'])
@login_required
def manage():
    """Display a user's account information."""
    return render_template('account/manage.html', user=current_user, form=None)


@account.route('/reset-password', methods=['GET', 'POST'])
@login_required
def reset_password_request():
    """Respond to existing user's request to reset their password."""
    if not current_user.is_anonymous:
        return redirect(url_for('main.index'))
    form = RequestResetPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            token = user.generate_password_reset_token()
            reset_link = url_for(
                'account.reset_password', token=token, _external=True)
            get_queue().enqueue(
                send_email,
                recipient=user.email,
                subject='Reset Your Password',
                template='account/email/reset_password',
                user=user,
                reset_link=reset_link,
                next=request.args.get('next'))
        flash('A password reset link has been sent to {}.'
              .format(form.email.data), 'warning')
        return redirect(url_for('account.login'))
    return render_template('account/reset_password.html', form=form)


@account.route('/reset-password/<token>', methods=['GET', 'POST'])
@login_required
def reset_password(token):
    """Reset an existing user's password."""
    if not current_user.is_anonymous:
        return redirect(url_for('main.index'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user is None:
            flash('Invalid email address.', 'form-error')
            return redirect(url_for('main.index'))
        if user.reset_password(token, form.new_password.data):
            flash('Your password has been updated.', 'form-success')
            return redirect(url_for('account.login'))
        else:
            flash('The password reset link is invalid or has expired.',
                  'form-error')
            return redirect(url_for('main.index'))
    return render_template('account/reset_password.html', form=form)


@account.route('/manage/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Change an existing user's password."""
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.verify_password(form.old_password.data):
            current_user.password = form.new_password.data
            db.session.add(current_user)
            db.session.commit()
            flash('Your password has been updated.', 'form-success')
            return redirect(url_for('main.index'))
        else:
            flash('Original password is invalid.', 'form-error')
    return render_template('account/manage.html', form=form)


@account.route('/manage/change-email', methods=['GET', 'POST'])
@login_required
def change_email_request():
    """Respond to existing user's request to change their email."""
    form = ChangeEmailForm()
    if form.validate_on_submit():
        if current_user.verify_password(form.password.data):
            new_email = form.email.data
            token = current_user.generate_email_change_token(new_email)
            change_email_link = url_for(
                'account.change_email', token=token, _external=True)
            get_queue().enqueue(
                send_email,
                recipient=new_email,
                subject='Confirm Your New Email',
                template='account/email/change_email',
                # current_user is a LocalProxy, we want the underlying user
                # object
                user=current_user._get_current_object(),
                change_email_link=change_email_link)
            flash('A confirmation link has been sent to {}.'.format(new_email),
                  'warning')
            return redirect(url_for('main.index'))
        else:
            flash('Invalid email or password.', 'form-error')
    return render_template('account/manage.html', form=form)


@account.route('/manage/change-email/<token>', methods=['GET', 'POST'])
@login_required
def change_email(token):
    """Change existing user's email with provided token."""
    if current_user.change_email(token):
        flash('Your email address has been updated.', 'success')
    else:
        flash('The confirmation link is invalid or has expired.', 'error')
    return redirect(url_for('main.index'))


@account.route('/confirm-account')
@login_required
def confirm_request():
    """Respond to new user's request to confirm their account."""
    token = current_user.generate_confirmation_token()
    confirm_link = url_for('account.confirm', token=token, _external=True)
    get_queue().enqueue(
        send_email,
        recipient=current_user.email,
        subject='Confirm Your Account',
        template='account/email/confirm',
        # current_user is a LocalProxy, we want the underlying user object
        user=current_user._get_current_object(),
        confirm_link=confirm_link)
    flash('A new confirmation link has been sent to {}.'.format(
        current_user.email), 'warning')
    return redirect(url_for('main.index'))


@account.route('/confirm-account/<token>')
@login_required
def confirm(token):
    """Confirm new user's account with provided token."""
    if current_user.has(Stage.COMPLETED_EMAIL_CONF):
        return redirect(url_for('main.index'))
    if current_user.confirm_account(token):
        flash('Your account has been confirmed.', 'success')
    else:
        flash('The confirmation link is invalid or has expired.', 'error')
    return redirect(url_for('main.index'))


@account.route(
    '/join-from-invite/<int:user_id>/<token>', methods=['GET', 'POST'])
def join_from_invite(user_id, token):
    """
    Confirm new user's account with provided token and prompt them to set
    a password.
    """
    if current_user is not None and current_user.is_authenticated:
        flash('You are already logged in.', 'error')
        return redirect(url_for('main.index'))

    new_user = User.query.get(user_id)
    if new_user is None:
        return redirect(404)

    if new_user.password_hash is not None:
        flash('You have already joined.', 'error')
        return redirect(url_for('main.index'))

    if new_user.confirm_account(token):
        form = CreatePasswordForm()
        if form.validate_on_submit():
            new_user.password = form.password.data
            db.session.add(new_user)
            db.session.commit()
            flash('Your password has been set. After you log in, you can '
                  'go to the "Your Account" page to review your account '
                  'information and settings.', 'success')
            return redirect(url_for('account.login'))
        return render_template('account/join_invite.html', form=form)
    else:
        flash('The confirmation link is invalid or has expired. Another '
              'invite email with a new link has been sent to you.', 'error')
        token = new_user.generate_confirmation_token()
        invite_link = url_for(
            'account.join_from_invite',
            user_id=user_id,
            token=token,
            _external=True)
        get_queue().enqueue(
            send_email,
            recipient=new_user.email,
            subject='You Are Invited To Join',
            template='account/email/invite',
            user=new_user,
            invite_link=invite_link)
    return redirect(url_for('main.index'))


@account.before_app_request
def before_request():
    """Force user to confirm email before accessing login-required routes."""
    if current_user.is_authenticated \
            and not current_user.has(Stage.COMPLETED_EMAIL_CONF) \
            and request.endpoint != 'static' \
            and request.endpoint != 'account.unconfirmed' \
            and request.endpoint != 'account.logout':
        return redirect(url_for('account.unconfirmed'))


@account.route('/unconfirmed')
def unconfirmed():
    """Catch users with unconfirmed emails."""
    if current_user.is_anonymous or current_user.has(Stage.COMPLETED_EMAIL_CONF):
        return redirect(url_for('main.index'))
    return render_template('account/unconfirmed.html')


def random_with_n_digits(n):
    range_start = 10**(n-1)
    range_end = (10**n)-1
    return random.randint(range_start, range_end)


@account.route('/primary_information', methods=['GET', 'POST'])
@login_required
def primary_information():
    form = ProfileForm()
    if current_user.has(Stage.COMPLETED_PRIMARY_INFO):
        # Primary Information
        form.primary.mobile_phone.data = current_user.mobile_phone
        form.primary.home_phone.data = current_user.home_phone
        # Geographic Information
        form.geographic.street.data = current_user.street
        form.geographic.city.data = current_user.city
        form.geographic.state.data = current_user.state
        form.geographic.zip.data = current_user.zip

    if form.validate_on_submit():
        flash('Thank you!', 'success')
        # Primary Information
        current_user.mobile_phone = form.primary.mobile_phone.data
        current_user.home_phone = form.primary.home_phone.data
        # Geographic Information
        current_user.street = form.geographic.street.data
        current_user.city = form.geographic.city.data
        current_user.state = form.geographic.state.data
        current_user.zip = form.geographic.zip.data
        current_user.stage |= Stage.COMPLETED_PRIMARY_INFO

        db.session.add(current_user)
        db.session.commit()
        return redirect(url_for('account.index'))

    return render_template('account/profile.html', form=form)


@account.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    airtable_html = SiteAttributes.query.all()[0].airtable_html
    return render_template('account/airtable.html', airtable_html=airtable_html)


@account.route('/manage/verify-phone', methods=['GET', 'POST'])
@login_required
def verify():
    form = VerifyPhoneNumberForm()
    if form.validate_on_submit():
        state = PhoneNumberState.query.filter_by(user_id=current_user.id).first()
        if str(state.verification_code) == form.code.data:
            flash('Your phone number has been verified.', 'success')
            current_user.mobile_phone = state.phone_number
            db.session.delete(state)
            db.session.commit()
            return redirect(url_for('account.index'))
        else:
            flash('Incorrect verification code', 'error')
            db.session.delete(state)
            db.session.commit()
            return redirect(url_for('account.applicant_info'))
    return render_template('account/verify.html', form=form)


@account.route('/modules-update', methods=['GET', 'POST'])
@login_required
@csrf.exempt
def modules_update():
    data = json.loads(request.form['data'])
    new_modules = list(current_user.modules)
    new_modules[data['module_num']] = {
        'filename': data['filename'],
        'certificate_url': data['certificate_url']
    }
    current_user.modules = new_modules
    db.session.add(current_user)
    db.session.commit()
    flash('Your progress has been updated.', 'success')
    return jsonify({'status': 200})


@account.route('/balance-update', methods=['GET', 'POST'])
@login_required
@csrf.exempt
def balance_update():
    balance = json.loads(request.form['balance'])
    current_user.bank_balance = balance
    db.session.add(current_user)
    db.session.commit()
    flash('Your balance has been updated.', 'success')
    return jsonify({'status': 200})


@account.route('/savings', methods=['GET', 'POST'])
@login_required
def savings():
    form = SavingsStartEndForm()
    if form.validate_on_submit():
        error_flag = False
        if form.start_date.data is not None and form.end_date.data is None:
            if form.start_date.data >= form.end_date.data:
                flash('The end date must be after the start date.', 'error')
                error_flag = True
        if not error_flag:
            current_user.savings_start_date = form.start_date.data
            current_user.savings_end_date = form.end_date.data
            flash('Your start and end dates have been saved.', 'success')
            db.session.commit()
    if current_user.savings_start_date is not None:
        form.start_date.data = current_user.savings_start_date
    if current_user.savings_end_date is not None:
        form.end_date.data = current_user.savings_end_date
    weeks = None
    if current_user.savings_start_date is not None and current_user.savings_end_date is not None:
        startd = current_user.savings_start_date
        endd = current_user.savings_end_date
        monday1 = (startd-timedelta(days=startd.weekday()))
        monday2 = (endd-timedelta(days=endd.weekday()))
        num_weeks = (monday2-monday1).days/7
        increment = current_user.goal_amount/float(num_weeks)
        weeks = []
        for i in range(int(num_weeks)):
            weeks.append(round(increment*(i+1), 2))
    return render_template('account/savings.html', form=form, weeks=weeks)


@account.route('/savingsHistory/', methods = ['GET', 'POST'])
def savings_history():
    
    form = SavingsHistoryForm()

    if form.validate_on_submit():
        savings = SavingsHistory(date=form.date.data, balance = form.balance.data, user_id=current_user.id)
        current_user.bank_balance = form.balance.data
        db.session.add(savings)
        db.session.commit()

    student_profile = SavingsHistory.query.filter_by(user_id=current_user.id).all()
    balance_array = []
    date_added = []

    if student_profile is not None:
        for i in range(len(student_profile)):
            balance_array.append(student_profile[i].balance)
            date_added.append(student_profile[i].date)

    return render_template('account/savings_history.html', form = form, 
        balance = balance_array, date = date_added, 
    lenBalance = len(balance_array), lenDate = len(date_added))


@account.route('/sign-s3/')
@login_required
def sign_s3():
    # Load necessary information into the application
    S3_BUCKET = os.environ.get('S3_BUCKET')
    S3_REGION = os.environ.get('S3_REGION')
    TARGET_FOLDER = 'json/'
    # Load required data from the request
    pre_file_name = request.args.get('file-name')
    file_name = ''.join(pre_file_name.split('.')[:-1]) + str(time.time()).replace('.','-') + '.' + ''.join(pre_file_name.split('.')[-1:])
    file_type = request.args.get('file-type')

    # Initialise the S3 client
    s3 = boto3.client('s3', 'us-west-2')

    # Generate and return the presigned URL
    presigned_post = s3.generate_presigned_post(
            Bucket = S3_BUCKET,
            Key = TARGET_FOLDER + file_name,
            Fields = {"acl": "public-read", "Content-Type": file_type},
            Conditions = [
                {"acl": "public-read"},
                {"Content-Type": file_type}
                ],
            ExpiresIn = 6000
            )

    # Return the data to the client
    return json.dumps({
        'data': presigned_post,
        'url_upload': 'https://%s.%s.amazonaws.com' % (S3_BUCKET, S3_REGION),
        'url': 'https://%s.amazonaws.com/%s/json/%s' % (S3_REGION, S3_BUCKET, file_name)
        })


@account.route('/about')
@login_required
def about():
    editable_html_obj = EditableHTML.get_editable_html('about')
    return render_template('account/editable.html',
                           editable_html_obj=editable_html_obj) 


@account.route('/resources')
@login_required
def resources():
    editable_html_obj = EditableHTML.get_editable_html('resources')
    return render_template('account/editable.html',
                           editable_html_obj=editable_html_obj)      

