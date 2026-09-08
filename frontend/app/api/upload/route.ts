import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { identity, allowedOrigin } from "@/lib/server";
export async function POST(request: Request) {
  if (!allowedOrigin(request)) return Response.json({error: "Origin rejected"}, {status: 403});
  try {
    const body = await request.json() as HandleUploadBody;
    const response = await handleUpload({body, request,
      onBeforeGenerateToken: async (pathname) => {
        const user = await identity(request);
        const uuid = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
        const pattern = new RegExp(`^workspaces/${user.workspace_id}/uploads/${uuid}/${uuid}\\.(pdf|csv)$`, "i");
        if (!pattern.test(pathname)) throw new Error("Invalid upload path");
        return {allowedContentTypes: ["application/pdf", "text/csv", "application/vnd.ms-excel"],
          maximumSizeInBytes: 25 * 1024 * 1024, addRandomSuffix: false,
          tokenPayload: JSON.stringify({workspace_id: user.workspace_id}),
        };
      },
      onUploadCompleted: async () => { /* Client explicitly registers the verified object as a job. */ },
    });
    return Response.json(response);
  } catch {
    return Response.json({error: "Upload authorization failed"}, {status: 403});
  }
}
