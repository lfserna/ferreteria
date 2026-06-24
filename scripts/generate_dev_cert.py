import ipaddress
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main():
    if len(sys.argv) < 2:
        print("Uso: python scripts/generate_dev_cert.py TU_IP_LOCAL")
        print("Ejemplo: python scripts/generate_dev_cert.py 192.168.1.35")
        raise SystemExit(1)

    ip_text = sys.argv[1].strip()
    local_ip = ipaddress.ip_address(ip_text)
    cert_dir = Path("certs")
    cert_dir.mkdir(exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "BO"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ferreteria Local Dev"),
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
        .not_valid_after(now + timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(local_ip),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    key_path = cert_dir / "dev-key.pem"
    cert_path = cert_dir / "dev-cert.pem"

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"Certificado creado: {cert_path}")
    print(f"Llave creada: {key_path}")
    print("Configura tu .env así:")
    print("APP_SSL=cert")
    print("APP_SSL_CERT=certs/dev-cert.pem")
    print("APP_SSL_KEY=certs/dev-key.pem")
    print(f"En el celular entra a: https://{ip_text}:5000")


if __name__ == "__main__":
    main()
