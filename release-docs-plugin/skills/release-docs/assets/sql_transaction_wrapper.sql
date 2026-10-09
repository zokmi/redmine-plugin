-- Unified deployment: run the entire file in one fresh connection/session.
SET XACT_ABORT ON;
DECLARE @ReleaseId nvarchar(128) = {{RELEASE_ID}};
DECLARE @ValidateOnly bit = {{VALIDATE_ONLY}};
DECLARE @Phase nvarchar(32) = N'PREFLIGHT';
DECLARE @UnitId nvarchar(128) = NULL;
DECLARE @SourcePath nvarchar(1024) = NULL;
DECLARE @SourceRevision nvarchar(64) = NULL;
DECLARE @SourceLine int = NULL;
DECLARE @TransactionAction nvarchar(64) = N'COMMIT NOT EXECUTED';

-- Do not inherit or roll back an operator's existing transaction.
IF @@TRANCOUNT <> 0
    THROW 51001, N'Use a fresh session with no active transaction.', 1;

-- An explicit BEGIN with implicit mode enabled can open two transactions.
SET IMPLICIT_TRANSACTIONS OFF;

BEGIN TRY
    BEGIN TRANSACTION;
{{UNITS}}
    SET @Phase = N'FINALIZE';
    IF @ValidateOnly = 1
    BEGIN
        ROLLBACK TRANSACTION;
        SET @TransactionAction = N'ROLLBACK';
    END
    ELSE IF @ValidateOnly = 0
    BEGIN
        COMMIT TRANSACTION;
        SET @TransactionAction = N'COMMIT';
    END
    ELSE
        THROW 51002, N'ValidateOnly must be 0 or 1.', 1;
END TRY
BEGIN CATCH
    DECLARE @ErrorNumber int = ERROR_NUMBER();
    DECLARE @ErrorSeverity int = ERROR_SEVERITY();
    DECLARE @ErrorState int = ERROR_STATE();
    DECLARE @ErrorProcedure nvarchar(128) = ERROR_PROCEDURE();
    DECLARE @ErrorLine int = ERROR_LINE();
    DECLARE @ErrorMessage nvarchar(4000) = ERROR_MESSAGE();
    DECLARE @ErrorXactState int = XACT_STATE();
    DECLARE @ErrorTranCount int = @@TRANCOUNT;
    IF XACT_STATE() <> 0
    BEGIN
        ROLLBACK TRANSACTION;
        SET @TransactionAction = N'ROLLBACK';
    END;
    SELECT @ReleaseId AS ReleaseId,
           DB_NAME() AS DatabaseName,
           CONVERT(nvarchar(128), SERVERPROPERTY('ServerName')) AS ServerName,
           @ValidateOnly AS ValidateOnly,
           @Phase AS Phase,
           @UnitId AS UnitId,
           @SourcePath AS SourcePath,
           @SourceRevision AS SourceRevision,
           @SourceLine AS SourceLine,
           @ErrorNumber AS ErrorNumber,
           @ErrorSeverity AS ErrorSeverity,
           @ErrorState AS ErrorState,
           @ErrorProcedure AS ErrorProcedure,
           @ErrorLine AS ErrorLine,
           CASE WHEN LOWER(@ErrorMessage) LIKE N'%password%'
                  OR LOWER(@ErrorMessage) LIKE N'%token%'
                  OR LOWER(@ErrorMessage) LIKE N'%secret%'
                  OR LOWER(@ErrorMessage) LIKE N'%connection string%'
                THEN N'<redacted sensitive error message>'
                ELSE @ErrorMessage END AS ErrorMessage,
           @ErrorXactState AS XactState,
           @ErrorTranCount AS TranCount,
           @TransactionAction AS TransactionAction,
           N'Execution stopped; resolve the failing unit before rerunning the entire artifact.' AS NextAction;
    THROW;
END CATCH;
