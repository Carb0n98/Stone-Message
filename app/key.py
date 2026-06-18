from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64

# Gerar chave privada
private_key = ec.generate_private_key(ec.SECP256R1())
private_key_bytes = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

# Gerar chave pública
public_key = private_key.public_key()
public_key_bytes = public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)

# Codificar as chaves em base64
vapid_private_key = base64.urlsafe_b64encode(private_key_bytes).decode('utf-8').rstrip('=')
vapid_public_key = base64.urlsafe_b64encode(public_key_bytes).decode('utf-8').rstrip('=')

print("VAPID Public Key: ", vapid_public_key)
print("VAPID Private Key: ", vapid_private_key)