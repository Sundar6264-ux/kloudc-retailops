import { createContext } from "react";

// How a photo answer is uploaded: null = the staff upload
// (POST /api/checklists/media, with the session). The public checklist link
// page provides its own (POST /api/public/checklists/{token}/media); it must
// return {id, url?} like the staff upload.
export const ChecklistUploadContext = createContext(null);
