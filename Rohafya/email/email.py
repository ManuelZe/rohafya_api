from flask_mail import Message, Mail
from flask import render_template
from Rohafya import create_app

#app = create_app()

#mail = Mail(app)

def send_email(to, subject, body, template_html, **context):
    app = create_app()

    mail = Mail(app)

    msg = Message(
        subject,
        recipients=[to],
        body=body,
        sender=app.config["MAIL_DEFAULT_SENDER"],
    )

    message_html = render_template(template_html, **context)


    msg.html=message_html
    mail.send(msg)
