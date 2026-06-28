import { Suspense } from "react";
import { WhatsappView } from "@/components/whatsapp";

export const metadata = {
  title: "WhatsApp · Dispatch",
};

export default function WhatsappPage() {
  return (
    <Suspense fallback={null}>
      <WhatsappView />
    </Suspense>
  );
}
