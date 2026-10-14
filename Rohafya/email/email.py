from flask_mail import Message, Mail
from flask import render_template
from Rohafya import create_app

#app = create_app()

#mail = Mail(app)

def send_email(to, subject, body, template_html, locale=None, **context):
    """E-mail des anciens modules, dans la langue du destinataire (voir Rohafya/saas/i18n.py) :
    `subject` peut être une clé de saas/i18n.py, `template_html` est remplacé par sa version anglaise si elle existe."""
    from Rohafya.saas import i18n

    app = create_app()

    mail = Mail(app)

    locale = locale if locale in i18n.LOCALES else i18n.email_locale(to)
    msg = Message(
        i18n.text(subject, locale, **context),
        recipients=[to],
        body=body,
        sender=app.config["MAIL_DEFAULT_SENDER"],
    )

    message_html = render_template(i18n.template_for(template_html, locale), locale=locale, **context)


    msg.html=message_html
    mail.send(msg)
