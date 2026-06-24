import ipaddress
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main():
    ip_text = sys.argv[1].strip() if len(sys.argv) > 1 else "192.168.10.13"
    local_ip = ipaddress.ip_address(ip_text)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ferreteria HTTPS Local"),
        x509.NameAttribute(NameOID.COMMON_NAME, ip_text),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(local_ip),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    Path("key.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    Path("cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print("Listo: cert.pem y key.pem generados en la raíz del proyecto.")
    print("Configura .env:")
    print("APP_SSL=true")
    print("APP_SSL_CERT=cert.pem")
    print("APP_SSL_KEY=key.pem")
    print(f"Abre en el celular: https://{ip_text}:5000")


if __name__ == "__main__":
    main()
