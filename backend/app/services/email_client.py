"""
Email Client

Client for sending notifications via SMTP email.

Features:
- HTML and plain text support
- TLS/SSL support
- Template-based emails
"""

import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class EmailClient:
    """
    Client for sending email notifications via SMTP.

    Supports both plain text and HTML emails with TLS encryption.
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_username: Optional[str] = None,
        smtp_password: Optional[str] = None,
        smtp_from: Optional[str] = None,
        use_tls: bool = True
    ):
        """
        Initialize email client.

        Args:
            smtp_host: SMTP server hostname
            smtp_port: SMTP server port (587 for TLS, 465 for SSL)
            smtp_username: SMTP authentication username
            smtp_password: SMTP authentication password
            smtp_from: From email address
            use_tls: Whether to use TLS encryption
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from or smtp_username
        self.use_tls = use_tls

    def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Send an email.

        Args:
            to: Recipient email address
            subject: Email subject
            body_text: Plain text body
            body_html: HTML body (optional)
            cc: CC recipients (optional)
            bcc: BCC recipients (optional)

        Returns:
            Dict with success status and any error message
        """
        if not self.smtp_host:
            return {'success': False, 'error': 'SMTP host not configured'}

        if not self.smtp_from:
            return {'success': False, 'error': 'From address not configured'}

        try:
            # Create message
            if body_html:
                msg = MIMEMultipart('alternative')
                msg.attach(MIMEText(body_text, 'plain'))
                msg.attach(MIMEText(body_html, 'html'))
            else:
                msg = MIMEText(body_text, 'plain')

            msg['Subject'] = subject
            msg['From'] = self.smtp_from
            msg['To'] = to

            if cc:
                msg['Cc'] = ', '.join(cc)

            # Build recipient list
            recipients = [to]
            if cc:
                recipients.extend(cc)
            if bcc:
                recipients.extend(bcc)

            # Send email
            if self.smtp_port == 465:
                # SSL
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                    if self.smtp_username and self.smtp_password:
                        server.login(self.smtp_username, self.smtp_password)
                    server.sendmail(self.smtp_from, recipients, msg.as_string())
            else:
                # TLS
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    if self.use_tls:
                        context = ssl.create_default_context()
                        server.starttls(context=context)
                    if self.smtp_username and self.smtp_password:
                        server.login(self.smtp_username, self.smtp_password)
                    server.sendmail(self.smtp_from, recipients, msg.as_string())

            logger.info(f"Email sent successfully to {to}")
            return {'success': True}

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            return {'success': False, 'error': 'SMTP authentication failed'}
        except smtplib.SMTPConnectError as e:
            logger.error(f"SMTP connection failed: {e}")
            return {'success': False, 'error': 'SMTP connection failed'}
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return {'success': False, 'error': f'SMTP error: {str(e)}'}
        except Exception as e:
            logger.error(f"Email send error: {e}")
            return {'success': False, 'error': str(e)}

    def _create_html_template(
        self,
        title: str,
        content: str,
        details: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Create HTML email from template.

        Args:
            title: Email title
            content: Main content
            details: Key-value pairs to display

        Returns:
            HTML string
        """
        details_html = ""
        if details:
            details_html = "<table style='margin-top: 20px; border-collapse: collapse;'>"
            for key, value in details.items():
                details_html += f"""
                <tr>
                    <td style='padding: 8px; border: 1px solid #ddd; font-weight: bold;'>{key}</td>
                    <td style='padding: 8px; border: 1px solid #ddd;'>{value}</td>
                </tr>
                """
            details_html += "</table>"

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #2563eb; color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; background-color: #f9fafb; }}
                .footer {{ padding: 10px; text-align: center; font-size: 12px; color: #666; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{title}</h1>
                </div>
                <div class="content">
                    <p>{content}</p>
                    {details_html}
                </div>
                <div class="footer">
                    Sent by Seedarr v2.0 at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
                </div>
            </div>
        </body>
        </html>
        """

    def send_upload_success(
        self,
        to: str,
        release_name: str,
        tracker_name: str,
        torrent_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send upload success notification.

        Args:
            to: Recipient email
            release_name: Name of the uploaded release
            tracker_name: Tracker name
            torrent_url: URL to the torrent

        Returns:
            Send result
        """
        subject = f"[Seedarr] Upload Successful - {release_name}"

        body_text = f"""
Upload Successful!

Release: {release_name}
Tracker: {tracker_name}
{'URL: ' + torrent_url if torrent_url else ''}

--
Seedarr v2.0
        """.strip()

        details = {
            'Release': release_name,
            'Tracker': tracker_name,
        }
        if torrent_url:
            details['URL'] = f'<a href="{torrent_url}">{torrent_url}</a>'

        body_html = self._create_html_template(
            title='Upload Successful',
            content=f'Your release has been successfully uploaded to {tracker_name}.',
            details=details
        )

        return self.send_email(to, subject, body_text, body_html)

    def send_upload_failed(
        self,
        to: str,
        release_name: str,
        tracker_name: str,
        error_message: str
    ) -> Dict[str, Any]:
        """
        Send upload failure notification.

        Args:
            to: Recipient email
            release_name: Name of the release
            tracker_name: Tracker name
            error_message: Error description

        Returns:
            Send result
        """
        subject = f"[Seedarr] Upload Failed - {release_name}"

        body_text = f"""
Upload Failed

Release: {release_name}
Tracker: {tracker_name}
Error: {error_message}

--
Seedarr v2.0
        """.strip()

        body_html = self._create_html_template(
            title='Upload Failed',
            content=f'Failed to upload {release_name} to {tracker_name}.',
            details={
                'Release': release_name,
                'Tracker': tracker_name,
                'Error': error_message
            }
        )

        return self.send_email(to, subject, body_text, body_html)

    def send_batch_complete(
        self,
        to: str,
        total: int,
        successful: int,
        failed: int
    ) -> Dict[str, Any]:
        """
        Send batch completion notification.

        Args:
            to: Recipient email
            total: Total files in batch
            successful: Successful uploads
            failed: Failed uploads

        Returns:
            Send result
        """
        status = "Complete" if failed == 0 else "Partial Success" if successful > 0 else "Failed"
        subject = f"[Seedarr] Batch {status} - {successful}/{total} uploaded"

        body_text = f"""
Batch Processing {status}

Total: {total}
Successful: {successful}
Failed: {failed}

--
Seedarr v2.0
        """.strip()

        body_html = self._create_html_template(
            title=f'Batch Processing {status}',
            content=f'Batch processing completed with {successful} successful and {failed} failed uploads.',
            details={
                'Total': str(total),
                'Successful': str(successful),
                'Failed': str(failed)
            }
        )

        return self.send_email(to, subject, body_text, body_html)

    def test_connection(self) -> Dict[str, Any]:
        """
        Test SMTP connection.

        Returns:
            Test result
        """
        try:
            if self.smtp_port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context, timeout=10) as server:
                    if self.smtp_username and self.smtp_password:
                        server.login(self.smtp_username, self.smtp_password)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                    if self.use_tls:
                        context = ssl.create_default_context()
                        server.starttls(context=context)
                    if self.smtp_username and self.smtp_password:
                        server.login(self.smtp_username, self.smtp_password)

            return {'success': True, 'message': 'SMTP connection successful'}

        except smtplib.SMTPAuthenticationError:
            return {'success': False, 'error': 'Authentication failed'}
        except smtplib.SMTPConnectError as e:
            return {'success': False, 'error': f'Connection failed: {str(e)}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
