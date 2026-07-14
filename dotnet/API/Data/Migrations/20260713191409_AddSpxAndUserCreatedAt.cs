using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace API.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddSpxAndUserCreatedAt : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<DateTime>(
                name: "CreatedAt",
                table: "AspNetUsers",
                type: "TEXT",
                nullable: false,
                defaultValue: new DateTime(1, 1, 1, 0, 0, 0, 0, DateTimeKind.Unspecified));

            migrationBuilder.CreateTable(
                name: "SpxCloses",
                columns: table => new
                {
                    Date = table.Column<string>(type: "TEXT", nullable: false),
                    Close = table.Column<double>(type: "REAL", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_SpxCloses", x => x.Date);
                });

            // Existing accounts predate this column: date them to their first
            // tracked card (a fair "started collecting" stand-in), else now.
            migrationBuilder.Sql(
                """
                UPDATE AspNetUsers SET CreatedAt = COALESCE(
                    (SELECT MIN(t.AddedAt) FROM TrackedCards t
                     WHERE t.UserName = AspNetUsers.UserName),
                    strftime('%Y-%m-%d %H:%M:%S', 'now'));
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "SpxCloses");

            migrationBuilder.DropColumn(
                name: "CreatedAt",
                table: "AspNetUsers");
        }
    }
}
