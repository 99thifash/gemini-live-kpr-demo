"""Diagnosa koneksi Gemini Live: daftar model + coba connect di v1beta dan v1alpha."""
import asyncio

from google import genai

MODEL = "gemini-3.8-live"

list_client = genai.Client()  # disimpan di variabel supaya tidak tertutup di tengah listing
names = [m.name for m in list_client.models.list()]
print(f"Total model: {len(names)}")
for n in names:
    print("  ", n)


async def try_connect(version):
    client = genai.Client(http_options={"api_version": version})
    try:
        async with client.aio.live.connect(
            model=MODEL, config={"response_modalities": ["AUDIO"]}
        ):
            return "OK, berhasil connect"
    except Exception as e:
        return f"GAGAL - {type(e).__name__}: {str(e)[:150]}"


async def main():
    for version in ["v1beta", "v1alpha"]:
        print(f"\n[{version}] {MODEL} -> {await try_connect(version)}")


asyncio.run(main())