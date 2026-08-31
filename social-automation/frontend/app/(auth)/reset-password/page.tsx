import ResetPasswordForm from './form';

export const dynamic = 'force-dynamic';

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  if (!token) {
    // If no token, show invalid link page (could also redirect)
    return (
      <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4 py-12">
        <div className="w-full max-w-md">
          <h1 className="text-2xl font-bold text-center mb-6">Invalid Reset Link</h1>
          <p className="text-center text-muted-foreground mb-6">
            This password reset link is invalid or has expired.
          </p>
          <a href="/forgot-password" className="inline-flex items-center px-4 py-2 bg-primary text-white rounded-md hover:bg-primary/90 transition-colors">
            Request New Reset Link
          </a>
        </div>
      </div>
    );
  }
  return <ResetPasswordForm token={token} />;
}
