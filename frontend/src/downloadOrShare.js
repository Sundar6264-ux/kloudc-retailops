// Web keeps the existing blob + <a download> click - browsers handle that
// natively. A Capacitor WebView doesn't reliably trigger that same download
// flow on-device, so on native the blob is written to the app's cache
// directory and handed to the OS share sheet instead (Save to Files,
// AirDrop, email, print, etc.) - the native equivalent of "downloading"
// something on a phone.
import { Capacitor } from "@capacitor/core";
import { Filesystem, Directory } from "@capacitor/filesystem";
import { Share } from "@capacitor/share";

export async function saveOrShareBlob(blob, filename) {
  if (!Capacitor.isNativePlatform()) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
    return;
  }

  const base64Data = await blobToBase64(blob);
  const written = await Filesystem.writeFile({
    path: filename,
    data: base64Data,
    directory: Directory.Cache,
  });
  await Share.share({ url: written.uri });
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      // reader.result is "data:<mime>;base64,<payload>" - Filesystem.writeFile
      // wants just the base64 payload after the comma.
      const commaIdx = reader.result.indexOf(",");
      resolve(reader.result.slice(commaIdx + 1));
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
