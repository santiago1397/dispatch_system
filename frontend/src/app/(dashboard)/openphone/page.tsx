import { Suspense } from "react";
import { OpenPhoneView } from "@/components/openphone";

export const metadata = {
  title: "OpenPhone · Dispatch",
};

export default function OpenPhonePage() {
  return (
    <Suspense fallback={null}>
      <OpenPhoneView />
    </Suspense>
  );
}
